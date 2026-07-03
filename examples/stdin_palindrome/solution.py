# CodeChef PRINCESS-style solution (stdin -> stdout).
#
# Reads T test cases; for each string, prints YES if it contains a palindromic
# substring of length > 1, else NO.
#
# This mirrors real competitive-programming solutions: it reads stdin at module
# top level (no `if __name__` guard), so it cannot be imported — LoopBench's
# run mode executes it as a subprocess instead.
#
# Generation 0 is a correct but deliberately naive O(n^3) check (it inspects
# every substring). LoopBench will optimize has_palindrome_substring() to O(n)
# while keeping the stdin/stdout behavior identical.


def has_palindrome_substring(s):
    """True if s has a palindromic substring of length > 1 (naive O(n^3))."""
    n = len(s)
    for i in range(n):
        for j in range(i + 2, n + 1):
            sub = s[i:j]
            if sub == sub[::-1]:
                return True
    return False


t = int(input())
for _ in range(t):
    s = input().strip()
    print("YES" if has_palindrome_substring(s) else "NO")
