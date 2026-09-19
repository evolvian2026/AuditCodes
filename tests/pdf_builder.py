"""Builds small PDFs in the question-bank template style, for cases the real sample lacks
(Python indentation, embedded figures, running footers across pages)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

LEFT = 25.5
CODE_LEFT = 31.6
PROSE = ("tiro", 9.2)
BOLD = ("tibo", 9.2)
CODE = ("cour", 7.7)
LINE = 13.5
CODE_LINE = 10.9


class TemplatePdf:
    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = None
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=595.28, height=841.89)
        self.y = 60.0
        # running header logo + footer like the real export
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 12), False)
        pix.clear_with(200)
        self.page.insert_image(pymupdf.Rect(LEFT, 14, LEFT + 75, 38), pixmap=pix)
        self.page.insert_text((LEFT, 828), "Coding Ques", fontsize=7.7, fontname="helv")
        self.page.insert_text((565, 828), str(len(self.doc)), fontsize=7.7, fontname="helv")

    def _ensure(self, needed: float) -> None:
        if self.y + needed > 800:
            self._new_page()

    def heading(self, text: str, indent: float = 0.0, blank: bool = True) -> None:
        self._ensure(LINE * 2)
        if blank:
            self.y += LINE * 0.6
        self.page.insert_text((LEFT + indent, self.y), text, fontsize=BOLD[1], fontname=BOLD[0])
        self.y += LINE

    def title(self, number: int, title: str, tail: str) -> None:
        self._ensure(LINE * 2)
        self.y += LINE
        bold = f"Q.{number} {title}"
        self.page.insert_text((LEFT, self.y), bold, fontsize=BOLD[1], fontname=BOLD[0])
        w = pymupdf.get_text_length(bold + " ", fontsize=BOLD[1], fontname=BOLD[0])
        self.page.insert_text((LEFT + w, self.y), tail, fontsize=PROSE[1], fontname=PROSE[0])
        self.y += LINE

    def prose(self, *lines: str, paragraph_gap: bool = False) -> None:
        for ln in lines:
            self._ensure(LINE)
            self.page.insert_text((LEFT, self.y), ln, fontsize=PROSE[1], fontname=PROSE[0])
            self.y += LINE * (2.5 if paragraph_gap else 1)

    def code(self, text: str, indent_pt: float = 19.3, left: float = CODE_LEFT) -> None:
        body = text.rstrip("\n").split("\n")
        self._ensure(CODE_LINE * (len(body) + 1))  # keep a block on one page, like the real export
        for ln in body:
            if not ln.strip():
                self.y += CODE_LINE
                continue
            spaces = len(ln) - len(ln.lstrip(" "))
            x = left + (spaces / 4) * indent_pt
            self.page.insert_text((x, self.y), ln.strip(), fontsize=CODE[1], fontname=CODE[0])
            self.y += CODE_LINE
        self.y += CODE_LINE * 0.5

    def image(self, width: float = 120, height: float = 40) -> None:
        self._ensure(height + LINE)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 20), False)
        pix.clear_with(90)
        self.page.insert_image(pymupdf.Rect(LEFT, self.y, LEFT + width, self.y + height), pixmap=pix)
        self.y += height + LINE * 0.5

    def case(self, label: str, stdin: str, stdout: str) -> None:
        self.heading(label, indent=8.9, blank=True)
        self.heading("Input", indent=8.9, blank=False)
        self.code(stdin, left=40.4)
        self.heading("Output", indent=8.9, blank=False)
        self.code(stdout, left=40.4)

    def save(self, path: Path) -> Path:
        self.doc.set_metadata({"title": "Synthetic Coding Ques"})
        self.doc.save(path)
        return path


PY_SOLUTION = '''def solve(a, b):
    if a > b:
        return a + b
    else:
        total = 0
        for x in (a, b):
            total += x
        return total


def main():
    a, b = map(int, input().split())
    print(solve(a, b))


main()
'''

PY_DRIVER = '''def solve(a, b):
    # Write your code here
    pass


def main():
    a, b = map(int, input().split())
    print(solve(a, b))


main()
'''


def build_python_question(path: Path) -> Path:
    pdf = TemplatePdf()
    pdf.heading("Questions", blank=False)
    pdf.title(7, "Indent Check", "Easy · Time limit 1 s")
    pdf.heading("DBNO: 555 Area: Basics LOD: 33", blank=False)
    pdf.heading("Problem Statement")
    pdf.prose("Print the sum of the two numbers shown in the figure.")
    pdf.image()
    pdf.prose("That is all there is to it.")
    pdf.heading("Input Explanation")
    pdf.prose("One line with two integers a and b.")
    pdf.heading("Output Explanation")
    pdf.prose("Print a + b.")
    pdf.heading("Constraints")
    pdf.prose("1 <= a, b <= 10")
    pdf.heading("Sample Test Cases (1)")
    pdf.case("Example 1", "1 2", "3")
    pdf.heading("Sample Test Case Explanation")
    pdf.prose("1 + 2 = 3.")
    pdf.heading("Driver Code - Python")
    pdf.code(PY_DRIVER)
    pdf.heading("Editorial")
    pdf.prose("Add them.")
    pdf.heading("Code Editorial - Python")
    pdf.code(PY_SOLUTION)
    pdf.heading("Hidden Test Cases (2)")
    pdf.case("Test Case 1 Level: Easy · Points: 5", "3 4", "7")
    pdf.case("Test Case 2 Level: Hard · Points: 15", "10 10", "20")
    return pdf.save(path)
