"""Reference problems with a known-good solution in every language.

Together they cover every scalar type, lists of every scalar, nested lists, empty inputs and
outputs, UTF-8 and JSON-escaped strings, and values that only fit in 64-bit integers.
"""

from __future__ import annotations

from dataclasses import dataclass

from auditcodes.models import IOSpec, Language, Param, TestCase


@dataclass
class Problem:
    name: str
    io_spec: IOSpec
    cases: list[TestCase]
    solutions: dict[Language, str]


def _spec(name, params, ret):
    return IOSpec(function_name=name, params=[Param(name=n, type=t) for n, t in params], return_type=ret)


def _cases(*pairs):
    return [TestCase(args=list(a), expected=e) for a, e in pairs]


PROBLEMS: list[Problem] = [
    Problem(
        "add",
        _spec("add", [("a", "int"), ("b", "int")], "int"),
        _cases(((1, 2), 3), ((-5, 5), 0), ((2147483647, 0), 2147483647), ((-2147483648, 0), -2147483648)),
        {
            Language.PYTHON: "def add(a, b):\n    return a + b\n",
            Language.JAVASCRIPT: "var add = function(a, b) { return a + b; };\n",
            Language.CPP: "class Solution {\npublic:\n    int add(int a, int b) { return a + b; }\n};\n",
            Language.JAVA: "class Solution {\n    public int add(int a, int b) { return a + b; }\n}\n",
            Language.C: "int add(int a, int b) { return a + b; }\n",
        },
    ),
    Problem(
        "sum_list",
        _spec("sumList", [("nums", "list<int>")], "long"),
        _cases((([1, 2, 3],), 6), (([],), 0), (([2147483647, 2147483647],), 4294967294), (([-1, -2],), -3)),
        {
            Language.PYTHON: "class Solution:\n    def sumList(self, nums):\n        return sum(nums)\n",
            Language.JAVASCRIPT: "class Solution {\n  sumList(nums) { let s = 0; for (const x of nums) s += x; return s; }\n}\n",
            Language.CPP: "class Solution {\npublic:\n    long long sumList(vector<int>& nums) { long long s = 0; for (int x : nums) s += x; return s; }\n};\n",
            Language.JAVA: "class Solution {\n    public long sumList(int[] nums) { long s = 0; for (int x : nums) s += x; return s; }\n}\n",
            Language.C: "long long sumList(int* nums, int numsSize) { long long s = 0; for (int i = 0; i < numsSize; i++) s += nums[i]; return s; }\n",
        },
    ),
    Problem(
        "reverse_string",
        _spec("reverseString", [("s", "string")], "string"),
        _cases((("abc",), "cba"), (("",), ""), (('a "q" \\ b\n\tc',), 'c\t\nb \\ "q" a'), (("x y",), "y x")),
        {
            Language.PYTHON: "def reverseString(s):\n    return s[::-1]\n",
            Language.JAVASCRIPT: "function reverseString(s) { return s.split('').reverse().join(''); }\n",
            Language.CPP: "class Solution {\npublic:\n    string reverseString(string s) { reverse(s.begin(), s.end()); return s; }\n};\n",
            Language.JAVA: "class Solution {\n    public String reverseString(String s) { return new StringBuilder(s).reverse().toString(); }\n}\n",
            Language.C: "char* reverseString(char* s) { int n = strlen(s); char* r = malloc(n + 1); for (int i = 0; i < n; i++) r[i] = s[n - 1 - i]; r[n] = 0; return r; }\n",
        },
    ),
    Problem(
        "repeat",
        _spec("repeat", [("s", "string"), ("n", "int")], "string"),
        _cases((("ab", 3), "ababab"), (("", 5), ""), (("héllo ✓", 2), "héllo ✓héllo ✓"), (("x", 0), "")),
        {
            Language.PYTHON: "def repeat(s, n):\n    return s * n\n",
            Language.JAVASCRIPT: "var repeat = function(s, n) { return s.repeat(n); };\n",
            Language.CPP: "class Solution {\npublic:\n    string repeat(const string& s, int n) { string r; for (int i = 0; i < n; i++) r += s; return r; }\n};\n",
            Language.JAVA: "class Solution {\n    public String repeat(String s, int n) { return s.repeat(n); }\n}\n",
            Language.C: "char* repeat(char* s, int n) { size_t L = strlen(s); char* r = malloc(L * n + 1); for (int i = 0; i < n; i++) memcpy(r + i * L, s, L); r[L * n] = 0; return r; }\n",
        },
    ),
    Problem(
        "transpose",
        _spec("transpose", [("matrix", "list<list<int>>")], "list<list<int>>"),
        _cases((([[1, 2], [3, 4]],), [[1, 3], [2, 4]]), (([[1, 2, 3]],), [[1], [2], [3]]), (([],), []), (([[7]],), [[7]])),
        {
            Language.PYTHON: "def transpose(matrix):\n    return [list(r) for r in zip(*matrix)]\n",
            Language.JAVASCRIPT: "var transpose = function(m) { if (m.length === 0) return []; return m[0].map((_, j) => m.map(r => r[j])); };\n",
            Language.CPP: (
                "class Solution {\npublic:\n    vector<vector<int>> transpose(vector<vector<int>>& m) {\n"
                "        if (m.empty()) return {};\n        vector<vector<int>> t(m[0].size(), vector<int>(m.size()));\n"
                "        for (size_t i = 0; i < m.size(); i++) for (size_t j = 0; j < m[0].size(); j++) t[j][i] = m[i][j];\n"
                "        return t;\n    }\n};\n"
            ),
            Language.JAVA: (
                "class Solution {\n    public int[][] transpose(int[][] m) {\n        if (m.length == 0) return new int[0][];\n"
                "        int[][] t = new int[m[0].length][m.length];\n"
                "        for (int i = 0; i < m.length; i++) for (int j = 0; j < m[0].length; j++) t[j][i] = m[i][j];\n"
                "        return t;\n    }\n}\n"
            ),
            Language.C: (
                "int** transpose(int** m, int mSize, int* mColSize, int* returnSize, int** returnColumnSizes) {\n"
                "    if (mSize == 0) { *returnSize = 0; *returnColumnSizes = NULL; return NULL; }\n"
                "    int cols = mColSize[0];\n    int** t = malloc(cols * sizeof(int*));\n    *returnColumnSizes = malloc(cols * sizeof(int));\n"
                "    for (int j = 0; j < cols; j++) { t[j] = malloc(mSize * sizeof(int)); (*returnColumnSizes)[j] = mSize;\n"
                "        for (int i = 0; i < mSize; i++) t[j][i] = m[i][j]; }\n    *returnSize = cols;\n    return t;\n}\n"
            ),
        },
    ),
    Problem(
        "is_positive",
        _spec("isPositive", [("x", "double")], "bool"),
        _cases(((1.5,), True), ((-0.001,), False), ((0.0,), False), ((1e-9,), True)),
        {
            Language.PYTHON: "def isPositive(x):\n    return x > 0\n",
            Language.JAVASCRIPT: "var isPositive = function(x) { return x > 0; };\n",
            Language.CPP: "class Solution {\npublic:\n    bool isPositive(double x) { return x > 0; }\n};\n",
            Language.JAVA: "class Solution {\n    public boolean isPositive(double x) { return x > 0; }\n}\n",
            Language.C: "bool isPositive(double x) { return x > 0; }\n",
        },
    ),
    Problem(
        "halve",
        _spec("halve", [("x", "int")], "double"),
        _cases(((3,), 1.5), ((-1,), -0.5), ((0,), 0.0), ((2000000000,), 1e9)),
        {
            Language.PYTHON: "def halve(x):\n    return x / 2\n",
            Language.JAVASCRIPT: "var halve = function(x) { return x / 2; };\n",
            Language.CPP: "class Solution {\npublic:\n    double halve(int x) { return x / 2.0; }\n};\n",
            Language.JAVA: "class Solution {\n    public double halve(int x) { return x / 2.0; }\n}\n",
            Language.C: "double halve(int x) { return x / 2.0; }\n",
        },
    ),
    Problem(
        "join_words",
        _spec("joinWords", [("words", "list<string>"), ("sep", "string")], "string"),
        _cases(((["a", "b", "c"], ", "), "a, b, c"), (([], "-"), ""), ((["x"], ""), "x"), ((["", ""], "|"), "|")),
        {
            Language.PYTHON: "def joinWords(words, sep):\n    return sep.join(words)\n",
            Language.JAVASCRIPT: "var joinWords = function(words, sep) { return words.join(sep); };\n",
            Language.CPP: "class Solution {\npublic:\n    string joinWords(vector<string>& w, string sep) { string r; for (size_t i = 0; i < w.size(); i++) { if (i) r += sep; r += w[i]; } return r; }\n};\n",
            Language.JAVA: "class Solution {\n    public String joinWords(String[] w, String sep) { return String.join(sep, w); }\n}\n",
            Language.C: (
                "char* joinWords(char** w, int wSize, char* sep) {\n    size_t total = 1; for (int i = 0; i < wSize; i++) total += strlen(w[i]) + strlen(sep);\n"
                "    char* r = malloc(total); r[0] = 0;\n    for (int i = 0; i < wSize; i++) { if (i) strcat(r, sep); strcat(r, w[i]); }\n    return r;\n}\n"
            ),
        },
    ),
    Problem(
        "negate_flags",
        _spec("negateFlags", [("flags", "list<bool>")], "list<bool>"),
        _cases((([True, False, False],), [False, True, True]), (([],), [])),
        {
            Language.PYTHON: "def negateFlags(flags):\n    return [not f for f in flags]\n",
            Language.JAVASCRIPT: "var negateFlags = function(flags) { return flags.map(f => !f); };\n",
            Language.CPP: "class Solution {\npublic:\n    vector<bool> negateFlags(vector<bool>& f) { vector<bool> r; for (bool b : f) r.push_back(!b); return r; }\n};\n",
            Language.JAVA: "class Solution {\n    public boolean[] negateFlags(boolean[] f) { boolean[] r = new boolean[f.length]; for (int i = 0; i < f.length; i++) r[i] = !f[i]; return r; }\n}\n",
            Language.C: "bool* negateFlags(bool* f, int fSize, int* returnSize) { bool* r = malloc(fSize * sizeof(bool) + 1); for (int i = 0; i < fSize; i++) r[i] = !f[i]; *returnSize = fSize; return r; }\n",
        },
    ),
    Problem(
        "add_long",
        _spec("addLong", [("a", "long"), ("b", "long")], "long"),
        _cases(((2**40, 2**40), 2**41), ((-1, 1), 0), ((-(2**50), 0), -(2**50))),
        {
            Language.PYTHON: "def addLong(a, b):\n    return a + b\n",
            Language.JAVASCRIPT: "var addLong = function(a, b) { return a + b; };\n",
            Language.CPP: "class Solution {\npublic:\n    long long addLong(long long a, long long b) { return a + b; }\n};\n",
            Language.JAVA: "class Solution {\n    public long addLong(long a, long b) { return a + b; }\n}\n",
            Language.C: "long long addLong(long long a, long long b) { return a + b; }\n",
        },
    ),
    Problem(
        "split_groups",
        _spec("splitGroups", [("words", "list<string>")], "list<list<string>>"),
        _cases(((["a", "b"],), [["a"], ["b"]]), (([],), []), ((["", "é"],), [[""], ["é"]])),
        {
            Language.PYTHON: "def splitGroups(words):\n    return [[w] for w in words]\n",
            Language.JAVASCRIPT: "var splitGroups = function(words) { return words.map(w => [w]); };\n",
            Language.CPP: "class Solution {\npublic:\n    vector<vector<string>> splitGroups(vector<string>& w) { vector<vector<string>> r; for (auto& s : w) r.push_back({s}); return r; }\n};\n",
            Language.JAVA: "class Solution {\n    public String[][] splitGroups(String[] w) { String[][] r = new String[w.length][]; for (int i = 0; i < w.length; i++) r[i] = new String[]{w[i]}; return r; }\n}\n",
            Language.C: (
                "char*** splitGroups(char** w, int wSize, int* returnSize, int** returnColumnSizes) {\n"
                "    char*** r = malloc((wSize + 1) * sizeof(char**)); *returnColumnSizes = malloc((wSize + 1) * sizeof(int));\n"
                "    for (int i = 0; i < wSize; i++) { r[i] = malloc(sizeof(char*)); r[i][0] = w[i]; (*returnColumnSizes)[i] = 1; }\n"
                "    *returnSize = wSize; return r;\n}\n"
            ),
        },
    ),
    Problem(
        "scale",
        _spec("scale", [("nums", "list<double>"), ("f", "double")], "list<double>"),
        _cases((([1.5, 2.0], 2.0), [3.0, 4.0]), (([], 3.0), []), (([1e-3, -7.25], 0.5), [0.0005, -3.625])),
        {
            Language.PYTHON: "def scale(nums, f):\n    return [x * f for x in nums]\n",
            Language.JAVASCRIPT: "var scale = function(nums, f) { return nums.map(x => x * f); };\n",
            Language.CPP: "class Solution {\npublic:\n    vector<double> scale(vector<double>& n, double f) { vector<double> r; for (double x : n) r.push_back(x * f); return r; }\n};\n",
            Language.JAVA: "class Solution {\n    public double[] scale(double[] n, double f) { double[] r = new double[n.length]; for (int i = 0; i < n.length; i++) r[i] = n[i] * f; return r; }\n}\n",
            Language.C: "double* scale(double* n, int nSize, double f, int* returnSize) { double* r = malloc(nSize * sizeof(double) + 1); for (int i = 0; i < nSize; i++) r[i] = n[i] * f; *returnSize = nSize; return r; }\n",
        },
    ),
]

BY_NAME = {p.name: p for p in PROBLEMS}
