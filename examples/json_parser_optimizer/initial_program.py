# EVOLVE-BLOCK-START
"""
LoopBench Demo — JSON Parser Optimizer
Generation 0: a correct but deliberately slow hand-written JSON parser.

Known bottleneck: tokens are accumulated with repeated string concatenation
(`result += ch`) one character at a time, and whitespace/number scanning walks
the input character by character in Python. This is correct but slow.

LoopBench will rewrite parse_json() to be faster while keeping every correctness
test green. The correctness contract is implementation-agnostic: the result must
equal json.loads(text), and trailing data must raise — verified by position,
not by any internal representation.
"""


def parse_json(text: str):
    """Parse a JSON document and return the equivalent Python object."""
    parser = _Parser(text)
    parser.skip_ws()
    value = parser.parse_value()
    parser.skip_ws()
    if parser.pos != len(parser.text):
        raise ValueError("Trailing data after JSON value")
    return value


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def _peek(self):
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\n\r":
            self.pos += 1

    def parse_value(self):
        self.skip_ws()
        ch = self._peek()
        if ch == "":
            raise ValueError("Unexpected end of input")
        if ch == "{":
            return self.parse_object()
        if ch == "[":
            return self.parse_array()
        if ch == '"':
            return self.parse_string()
        if ch == "t" or ch == "f":
            return self.parse_bool()
        if ch == "n":
            return self.parse_null()
        return self.parse_number()

    def parse_object(self):
        obj = {}
        self.pos += 1  # consume '{'
        self.skip_ws()
        if self._peek() == "}":
            self.pos += 1
            return obj
        while True:
            self.skip_ws()
            key = self.parse_string()
            self.skip_ws()
            if self._peek() != ":":
                raise ValueError("Expected ':' in object")
            self.pos += 1  # consume ':'
            obj[key] = self.parse_value()
            self.skip_ws()
            ch = self._peek()
            self.pos += 1
            if ch == "}":
                return obj
            if ch != ",":
                raise ValueError("Expected ',' or '}' in object")

    def parse_array(self):
        arr = []
        self.pos += 1  # consume '['
        self.skip_ws()
        if self._peek() == "]":
            self.pos += 1
            return arr
        while True:
            arr.append(self.parse_value())
            self.skip_ws()
            ch = self._peek()
            self.pos += 1
            if ch == "]":
                return arr
            if ch != ",":
                raise ValueError("Expected ',' or ']' in array")

    def parse_string(self):
        if self._peek() != '"':
            raise ValueError("Expected string")
        self.pos += 1  # consume opening quote
        result = ""  # BOTTLENECK: repeated concatenation, char by char
        while True:
            ch = self._peek()
            if ch == "":
                raise ValueError("Unterminated string")
            self.pos += 1
            if ch == '"':
                return result
            if ch == "\\":
                esc = self._peek()
                self.pos += 1
                mapping = {
                    '"': '"', "\\": "\\", "/": "/", "b": "\b",
                    "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                }
                if esc in mapping:
                    result += mapping[esc]
                elif esc == "u":
                    hexcode = self.text[self.pos:self.pos + 4]
                    self.pos += 4
                    result += chr(int(hexcode, 16))
                else:
                    raise ValueError(f"Invalid escape: \\{esc}")
            else:
                result += ch

    def parse_number(self):
        num = ""  # BOTTLENECK: repeated concatenation, char by char
        while self.pos < len(self.text) and self.text[self.pos] in "-+.eE0123456789":
            num += self.text[self.pos]
            self.pos += 1
        if not num:
            raise ValueError("Invalid number")
        if any(c in num for c in ".eE"):
            return float(num)
        return int(num)

    def parse_bool(self):
        if self.text[self.pos:self.pos + 4] == "true":
            self.pos += 4
            return True
        if self.text[self.pos:self.pos + 5] == "false":
            self.pos += 5
            return False
        raise ValueError("Invalid literal")

    def parse_null(self):
        if self.text[self.pos:self.pos + 4] == "null":
            self.pos += 4
            return None
        raise ValueError("Invalid literal")


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_parse(text: str):
    """Public entry point called by the evaluator and tests."""
    return parse_json(text)


if __name__ == "__main__":
    import json
    import time

    sample = json.dumps({"items": [{"id": i, "name": f"n{i}", "ok": True} for i in range(500)]})
    start = time.perf_counter()
    parsed = run_parse(sample)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"parsed {len(parsed['items'])} items")
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
    print(f"Time: {elapsed_ms:.1f}ms")
