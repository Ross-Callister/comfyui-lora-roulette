"""Seeded prompt wildcards using the common dynamicprompts syntax.

    {a|b|c}             pick one                      {a|{b|c}}        nesting
    {3::a|1::b}         weighted pick (default 1)     {2$$a|b|c}       pick 2 distinct, joined ", "
    {2-3$$a|b|c}        pick 2 or 3                   {-2$$...}        pick 1-2;  {2-$$...} 2-all
    {2$$ and $$a|b|c}   custom separator

// and /* */ comments are removed; \\{ \\} \\| produce literal characters.
"""

import re

_BOUNDS = re.compile(r"\s*(\d*)\s*(-?)\s*(\d*)\s*\$\$(?:([^${}|]*)\$\$)?")
_WEIGHT = re.compile(r"\s*(\d+(?:\.\d+)?|\.\d+)\s*::")
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


class _Choice:
    def __init__(self, options, lo, hi, sep):
        self.options, self.lo, self.hi, self.sep = options, lo, hi, sep


class _Parser:
    def __init__(self, text):
        self.s, self.i = text, 0

    def parse(self):
        return self._seq("")

    def _seq(self, stops):
        parts, buf = [], []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\" and self.s[self.i + 1:self.i + 2] in ("{", "}", "|"):
                buf.append(self.s[self.i + 1])
                self.i += 2
            elif c in stops:
                break
            elif c == "{":
                _flush(buf, parts)
                self.i += 1
                parts.append(self._choice())
            else:
                buf.append(c)
                self.i += 1
        _flush(buf, parts)
        return parts

    def _weight(self):
        m = _WEIGHT.match(self.s, self.i)
        if not m:
            return 1.0
        self.i = m.end()
        return float(m.group(1))

    def _choice(self):
        start = self.i - 1
        lo, hi, sep = 1, 1, ", "
        m = _BOUNDS.match(self.s, self.i)
        if m:
            a, dash, b, sep_text = m.groups()
            if dash:
                lo, hi = (int(a) if a else 1), (int(b) if b else None)
            else:
                lo = hi = int(a) if a else 1
            if sep_text is not None:
                sep = sep_text
            self.i = m.end()
        options = []
        while True:
            w = self._weight()
            options.append((w, _strip(self._seq("|}"))))
            if self.i >= len(self.s):
                raise ValueError(f"unclosed '{{' in prompt: {self.s[start:start + 40]!r}")
            closing = self.s[self.i] == "}"
            self.i += 1
            if closing:
                return _Choice(options, lo, hi, sep)


def _flush(buf, parts):
    if buf:
        parts.append("".join(buf))
        buf.clear()


def _strip(seq):
    if seq and isinstance(seq[0], str):
        seq[0] = seq[0].lstrip()
    if seq and isinstance(seq[-1], str):
        seq[-1] = seq[-1].rstrip()
    return [p for p in seq if p != ""]


def _render(seq, rng):
    return "".join(p if isinstance(p, str) else _choose(p, rng) for p in seq)


def _choose(choice, rng):
    hi = len(choice.options) if choice.hi is None else min(choice.hi, len(choice.options))
    lo = min(choice.lo, hi)
    pool = [o for o in choice.options if o[0] > 0]
    picked = []
    for _ in range(min(rng.randint(lo, hi), len(pool))):
        r = rng.uniform(0, sum(w for w, _ in pool))
        for idx, (w, _) in enumerate(pool):
            r -= w
            if r <= 0:
                break
        picked.append(pool.pop(idx)[1])
    return choice.sep.join(_render(s, rng) for s in picked)


def expand(text, rng):
    """Expand wildcard syntax in text using rng (a random.Random)."""
    return _render(_Parser(_COMMENTS.sub("", text)).parse(), rng)
