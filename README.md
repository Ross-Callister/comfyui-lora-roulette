# LoRA Roulette

A single ComfyUI node for testing LoRAs in combination. It reads a text list of LoRAs, picks a combination (random, or a sweep through every unique combination), applies the LoRAs, appends their trigger words to the prompt, and builds a filename that names the combination.

## Install

Copy this folder into `ComfyUI/custom_nodes/` and restart ComfyUI. It has no pip dependencies. Then load `../lora_roulette_workflow.json`.

## List format

```
# illustrious/old_experiment ; unused
illustrious\add_detail_xl.safetensors;add detail
cinematic_lighting ; cinematic lighting, rim light ; 0.7/1.0
illustrious/rainy_weather ; rain, wet ; 0.3-0.8
!illustrious/Princess_Luna-000007 ; princess luna (mlp) ; 0.9
```

The lines above, in order:

1. Disabled, because it starts with `#`. Comments must start the line; a `#` later in a line is read as part of the trigger.
2. The old `path;trigger` format, unchanged.
3. Fixed model/clip strength, given by bare filename.
4. Random model strength between 0.3 and 0.8.
5. Pinned with `!`, so it goes on every image on top of the random picks.

- `\` and `/` are interchangeable, and the `.safetensors` extension is optional. A bare filename matches in any subfolder if the name is unique.
- If a line doesn't match any file, the run stops with an error that lists the bad lines. This stops typos from quietly shrinking the pool.
- `None;...` lines are ignored. Setting `min_loras` to 0 gives you runs with no LoRA.

## Inputs

| input | meaning |
|---|---|
| `mode` | `random`: the seed picks `min_loras..max_loras` LoRAs. `sweep`: the seed is an index into every unique combination, so set the seed to *increment*. |
| `seed` | Sets both the pick and any random strengths. The same seed and list always give the same pick. |
| `strength_min/max` | Model-strength range for lines that don't set their own. Set them equal for a fixed strength. |
| `clip_strength` | CLIP strength for lines that don't set one with `model/clip`. |
| `prompt` | Positive prompt with wildcards (see below). The node expands it, not the browser. |
| `prompt_seed` | Seed for the wildcards, separate from the LoRA pick. Set it to *fixed* to keep the prompt while the LoRAs change. |

The `info` output (shown in "Picked LoRAs") lists each LoRA with its strengths. In sweep mode it also shows the position as `sweep i/total`.

## Prompt wildcards

The node expands these itself, seeded by `prompt_seed`, using the same inline syntax as comfyui-dynamicprompts:

| syntax | result |
|---|---|
| `{a\|b\|c}` | one option, picked evenly |
| `{a\|{b\|c}}` | nesting; each level picks evenly among its own options |
| `{3::a\|1::b}` | weighted pick; the default weight is 1 and 0 disables an option |
| `{2$$a\|b\|c}` | 2 distinct options, joined with `, ` |
| `{2-3$$a\|b\|c}`, `{-2$$...}`, `{2-$$...}` | 2-3 options; 1-2; 2 up to all |
| `{2$$ and $$a\|b\|c}` | custom separator |
| `//`, `/* */` | comments, which are removed |
| `\{ \} \|` | literal characters |

Wildcard files (`__name__`) aren't supported; `__name__` is left as plain text. If a `{` is never closed, the run stops with an error.

The expanded prompt is printed in the `info` output. Because the expansion depends only on `prompt_seed` and the template, the same seed always gives the same prompt.

Tests: `uv run --with pytest pytest tests` from the project root.
