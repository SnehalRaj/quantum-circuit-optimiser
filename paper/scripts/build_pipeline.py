#!/usr/bin/env python3
"""Draw an editable SVG and export it with Inkscape."""
from html import escape
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "figures"
INK = "#193343"
GRAY = "#556b79"
BLUE = "#23759a"
TEAL = "#167d73"
PURPLE = "#8060a2"


def main():
    FIG.mkdir(exist_ok=True)
    parts = ['''<svg xmlns="http://www.w3.org/2000/svg"
      xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
      width="180mm" height="96mm" viewBox="0 0 1600 854">
      <title>Setup, fast search loop, and submission phase</title>
      <desc>Setup collects GitHub commits and messages, creates point-addition
      benchmarks, and creates a typed grammar. The fast loop selects parents,
      proposes edits, checks circuits, and promotes candidates. The submission phase
      validates full point addition and submits qualified circuits.</desc>
      <defs>''']
    for key, color in [('gray', GRAY), ('blue', BLUE), ('teal', TEAL), ('purple', PURPLE)]:
        parts.append(f'''<marker id="arrow-{key}" markerWidth="10" markerHeight="10"
          refX="8" refY="5" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M 0 0 L 9 5 L 0 10 Z" fill="{color}"/></marker>''')
    parts += ['</defs>', '<rect width="1600" height="854" fill="white"/>']

    def text(x, y, value, size=22, color=GRAY, weight=400, anchor='start'):
        parts.append(f'<text x="{x}" y="{y}" font-family="DejaVu Sans, sans-serif" '
                     f'font-size="{size}" fill="{color}" font-weight="{weight}" '
                     f'text-anchor="{anchor}">{escape(value)}</text>')

    def rect(x, y, w, h, fill='white', stroke='#c1d2dc', radius=12, width=1.8):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def path(d, key='gray', dashed=False, arrow=True, width=2.5):
        color = {'gray': GRAY, 'blue': BLUE, 'teal': TEAL, 'purple': PURPLE}[key]
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                     f'stroke-linejoin="round" stroke-linecap="round" '
                     + ('stroke-dasharray="9 7" ' if dashed else '')
                     + (f'marker-end="url(#arrow-{key})" ' if arrow else '') + '/>')

    def card(x, y, w, title, lines, number=None, color=BLUE, h=136):
        rect(x, y, w, h, stroke=color)
        if number is not None:
            parts.append(f'<circle cx="{x+27}" cy="{y+31}" r="17" fill="{color}"/>')
            text(x+27, y+38, str(number), 21, 'white', 700, 'middle')
            title_x = x+55
        else:
            title_x = x+22
        text(title_x, y+40, title, 24, INK, 700)
        for i, line in enumerate(lines):
            text(x+22, y+76+i*28, line)

    rect(40, 12, 1520, 196, '#f7f9fb', '#d8e0e5', 16)
    text(62, 48, 'SETUP', 25, INK, 700)
    card(64, 69, 464, 'Data gathering and cleaning',
         ['GitHub commits and commit messages', 'Source code and research notes'], h=119)
    card(564, 69, 464, 'Benchmark creation',
         ['16-bit point addition', 'Full secp256k1 point addition'], h=119)
    card(1064, 69, 472, 'Typed grammar creation',
         ['Arithmetic edits', 'Conditions for each edit'], h=119)
    path('M 530 128 H 555')
    path('M 1030 128 H 1055')

    rect(40, 254, 1520, 365, '#f2f8fb', '#d4e6ef', 16)
    text(62, 289, 'FAST LOOP', 25, BLUE, 700)
    card(128, 319, 480, 'Select parent', [], h=100)
    card(992, 319, 480, 'Propose edits', ['LLM or model-free'], h=100)
    card(992, 475, 480, 'Build and check', ['16-bit point addition'], h=100)
    card(128, 475, 480, 'Promote selected circuits', [], h=100)
    path('M 610 369 H 982', 'blue')
    path('M 1232 421 V 465', 'blue')
    path('M 990 525 H 618', 'blue')
    path('M 126 525 H 86 V 369 H 118', 'blue')
    text(800, 452, 'Repeat the search', 23, BLUE, anchor='middle')
    path('M 296 189 V 230 H 368 V 309', 'gray')
    path('M 1300 189 V 230 H 1232 V 309', 'blue')
    path('M 796 189 V 239 H 1544 V 525 H 1482', 'gray')

    rect(40, 670, 1520, 166, '#f0f8f5', '#d1e4dd', 16)
    text(62, 705, 'SUBMISSION PHASE', 25, TEAL, 700)
    path('M 368 577 V 715', 'teal', width=3)
    card(64, 722, 698, 'Run and validate',
         ['Full point-addition benchmarks'], color=TEAL, h=94)
    card(838, 722, 698, 'Submit to leaderboard',
         ['Validated circuits'], color=TEAL, h=94)
    path('M 764 769 H 828', 'teal')
    parts.append('</svg>')
    svg = FIG / 'fig2_pipeline.svg'
    svg.write_text('\n'.join(parts)+'\n')
    inkscape = shutil.which('inkscape')
    if not inkscape:
        app = Path('/Applications/Inkscape.app/Contents/MacOS/inkscape')
        inkscape = str(app) if app.exists() else None
    if not inkscape:
        raise SystemExit('Install Inkscape to export the editable pipeline SVG.')
    for suffix in ('pdf', 'png'):
        args = [inkscape, str(svg), '--export-area-page', f'--export-type={suffix}',
                f'--export-filename={FIG / ("fig2_pipeline."+suffix)}']
        if suffix == 'png':
            args.append('--export-dpi=240')
        subprocess.run(args, check=True, capture_output=True, text=True)


if __name__ == '__main__':
    main()
