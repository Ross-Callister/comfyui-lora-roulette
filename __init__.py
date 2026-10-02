"""LoRA Roulette: pick a combination of LoRAs from a text list and apply them in one node.

List format, one LoRA per line (the old "path;trigger" lists paste in unchanged):

    path/to/lora.safetensors ; trigger words ; strength

- strength is optional: "0.6" (model), "0.6/1.0" (model/clip) or "0.3-0.8" (random model range).
  Without it, the node's strength_min..strength_max range is used.
- "#" at the start disables a line. "!" at the start pins it: it is applied to every image,
  on top of the min_loras..max_loras picked from the rest.
- "\\" and "/" are interchangeable, and a bare filename matches in any subfolder, so lists
  move between Windows and Linux installs.

The prompt supports dynamicprompts-style wildcards, expanded here with prompt_seed (see wildcards.py):
{a|b}, nesting, {3::a|1::b} weights, {2-3$$a|b|c} multi-pick and {2$$ and $$...} separators.
"""

import itertools
import math
import os
import random
import re
from collections import OrderedDict

import comfy.sd
import comfy.utils
import folder_paths

from .wildcards import expand

_LORA_CACHE = OrderedDict()
_LORA_CACHE_SIZE = 8


def _load_lora(path):
    if path in _LORA_CACHE:
        _LORA_CACHE.move_to_end(path)
        return _LORA_CACHE[path]
    _LORA_CACHE[path] = comfy.utils.load_torch_file(path, safe_load=True, return_metadata=True)
    while len(_LORA_CACHE) > _LORA_CACHE_SIZE:
        _LORA_CACHE.popitem(last=False)
    return _LORA_CACHE[path]


def _norm(name):
    return name.replace("\\", "/").strip().lower()


def _resolve(name, available):
    """Match a list entry to a file in models/loras, ignoring slash direction, case and extension."""
    want = _norm(name)
    by_path = {_norm(a): a for a in available}
    for cand in (want, want + ".safetensors"):
        if cand in by_path:
            return by_path[cand]
    base = os.path.basename(want)
    hits = [a for a in available if os.path.basename(_norm(a)) in (base, base + ".safetensors")]
    return hits[0] if len(hits) == 1 else None


def _parse_strength(text):
    """'0.6' -> (0.6, 0.6, None); '0.6/1.0' -> (0.6, 0.6, 1.0); '0.3-0.8' -> (0.3, 0.8, None)."""
    text = text.strip()
    if not text:
        return None
    clip = None
    if "/" in text:
        text, clip_text = text.split("/", 1)
        clip = float(clip_text)
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*", text)
    if m:
        return float(m.group(1)), float(m.group(2)), clip
    return float(text), float(text), clip


def parse_list(text, available):
    entries, missing = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pinned = line.startswith("!")
        parts = [p.strip() for p in line.lstrip("!").split(";")]
        name = parts[0]
        if name.lower() in ("none", ""):
            continue
        resolved = _resolve(name, available)
        if resolved is None:
            missing.append(name)
            continue
        try:
            strength = _parse_strength(parts[2]) if len(parts) > 2 else None
        except ValueError:
            raise ValueError(f"LoRA Roulette: bad strength '{parts[2]}' on line: {raw}")
        entries.append({
            "file": resolved,
            "trigger": parts[1] if len(parts) > 1 else "",
            "strength": strength,
            "pinned": pinned,
        })
    return entries, missing


def _short(entry):
    stem = os.path.splitext(os.path.basename(entry["file"].replace("\\", "/")))[0]
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:28]


class LoraRoulette:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": False,
                                      "tooltip": "Positive prompt; the chosen LoRAs' trigger words are appended. Wildcards: {a|b}, nesting, "
                                                 "{3::a|1::b} weights, {2-3$$a|b|c} multi-pick, {2$$ and $$a|b|c} separator."}),
                "prompt_seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True,
                                        "tooltip": "Seed for the prompt wildcards, separate from the LoRA pick. 'fixed' keeps the prompt while LoRAs change."}),
                "lora_list": ("STRING", {"multiline": True, "default": "# path ; trigger ; strength(optional)\n",
                                         "tooltip": "One LoRA per line: path ; trigger ; strength. '#' disables a line, '!' pins it to every image."}),
                "mode": (["random", "sweep"], {"tooltip": "random: seed picks a random combination. sweep: seed is an index that walks every unique combination once (set the seed to increment)."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True,
                                 "tooltip": "Chooses the combination and random strengths. Keep the KSampler seed fixed so images differ only by LoRAs."}),
                "min_loras": ("INT", {"default": 2, "min": 0, "max": 16, "tooltip": "Fewest unpinned LoRAs per image."}),
                "max_loras": ("INT", {"default": 2, "min": 0, "max": 16, "tooltip": "Most unpinned LoRAs per image."}),
                "strength_min": ("FLOAT", {"default": 0.4, "min": -4.0, "max": 4.0, "step": 0.05,
                                           "tooltip": "Model strength range for lines without their own strength. Set min = max for a fixed strength."}),
                "strength_max": ("FLOAT", {"default": 0.8, "min": -4.0, "max": 4.0, "step": 0.05}),
                "clip_strength": ("FLOAT", {"default": 1.0, "min": -4.0, "max": 4.0, "step": 0.05,
                                            "tooltip": "CLIP strength for lines that don't set one with model/clip."}),
                "filename_prefix": ("STRING", {"default": "lora_roulette/"}),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("model", "clip", "positive", "filename", "info")
    OUTPUT_TOOLTIPS = (
        "Model with the chosen LoRAs applied.",
        "CLIP with the chosen LoRAs applied.",
        "The wildcard-expanded prompt with the chosen trigger words appended.",
        "filename_prefix plus the combination, e.g. lora_roulette/add_detail_xl-0.55__cinematic_lighting-0.70. Wire it to Save Image.",
        "Human-readable summary of the pick, including the sweep position.",
    )
    FUNCTION = "apply"
    CATEGORY = "loaders/lora testing"
    DESCRIPTION = "Picks a random (or sweep-ordered) combination of LoRAs from a text list, applies them, and appends their trigger words to the prompt."

    def apply(self, model, clip, prompt, prompt_seed, lora_list, mode, seed, min_loras, max_loras,
              strength_min, strength_max, clip_strength, filename_prefix):
        try:
            prompt = expand(prompt, random.Random(prompt_seed))
        except ValueError as e:
            raise ValueError(f"LoRA Roulette prompt: {e}")
        entries, missing = parse_list(lora_list, folder_paths.get_filename_list("loras"))
        if missing:
            raise ValueError("LoRA Roulette: these list entries don't match any file in models/loras "
                             "(fix them or disable with #):\n  " + "\n  ".join(missing))
        pinned = [e for e in entries if e["pinned"]]
        pool = [e for e in entries if not e["pinned"]]
        lo, hi = sorted((min(min_loras, len(pool)), min(max_loras, len(pool))))
        rng = random.Random(seed)

        if mode == "sweep":
            sizes = list(range(lo, hi + 1))
            total = sum(math.comb(len(pool), k) for k in sizes)
            index = seed % total if total else 0
            position = f"sweep {index + 1}/{total}"
            for k in sizes:
                n = math.comb(len(pool), k)
                if index < n:
                    chosen = list(next(itertools.islice(itertools.combinations(pool, k), index, None)))
                    break
                index -= n
        else:
            chosen = rng.sample(pool, rng.randint(lo, hi))
            position = "random"

        applied, triggers, labels, lines = [], [], [], []
        for e in pinned + chosen:
            s_lo, s_hi, s_clip = e["strength"] or (strength_min, strength_max, None)
            sm = round(rng.uniform(min(s_lo, s_hi), max(s_lo, s_hi)) / 0.05) * 0.05
            sc = clip_strength if s_clip is None else s_clip
            lora, meta = _load_lora(folder_paths.get_full_path_or_raise("loras", e["file"]))
            model, clip = comfy.sd.load_lora_for_models(model, clip, lora, sm, sc, lora_metadata=meta)
            if e["trigger"]:
                triggers.append(e["trigger"])
            labels.append(f"{_short(e)}-{sm:.2f}")
            lines.append(f"{'! ' if e['pinned'] else ''}{e['file']}  model {sm:.2f} / clip {sc:.2f}  [{e['trigger']}]")
            applied.append(e)

        positive = ", ".join(p for p in (prompt.strip().rstrip(","), ", ".join(triggers)) if p)
        filename = filename_prefix + ("__".join(labels) or "no_lora")
        info = (f"{position}, seed {seed}, {len(pool)} in pool, {len(pinned)} pinned\n" + ("\n".join(lines) or "(no LoRAs)")
                + f"\n\nprompt (seed {prompt_seed}): {prompt}")
        print(f"[LoRA Roulette] {info}")
        return (model, clip, positive, filename, info)


NODE_CLASS_MAPPINGS = {"LoraRoulette": LoraRoulette}
NODE_DISPLAY_NAME_MAPPINGS = {"LoraRoulette": "LoRA Roulette"}
