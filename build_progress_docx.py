"""Build the whole-system progress tracker (diagram + tick-able checklist) as a DOCX.

Run: python build_progress_docx.py
Output: System-Progress-Tracker.docx (+ system_flow.png alongside)
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

HERE = __file__.rsplit("\\", 1)[0]
PNG = HERE + "\\system_flow.png"
DOCX = HERE + "\\System-Progress-Tracker.docx"

# ---- status palette -------------------------------------------------------
GREEN = ("#2e7d32", "#c8e6c9")   # Working / verified
AMBER = ("#f9a825", "#fff3c4")   # Built - to verify / Pending
RED = ("#c62828", "#ffcdd2")     # Needs attention
GREY = ("#616161", "#e0e0e0")    # Not started / unknown
LANE = "#eef2f7"

# ===========================================================================
# 1. The flow diagram
# ===========================================================================
def draw_diagram() -> None:
    fig, ax = plt.subplots(figsize=(16, 9.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    lanes = [
        ("AgriTrack Mobile App  (Farmer)", 82, 96),
        ("Gateway  (AgriTrack backend - owns identity + geometry)", 64, 80),
        ("remote-sense / Sentinel  (satellite intelligence)", 34, 62),
        ("agriAnalysis  (agronomic analysis web app)", 16, 32),
        ("Results consumers", 0, 14),
    ]
    for label, y0, y1 in lanes:
        ax.add_patch(FancyBboxPatch((0.5, y0), 99, y1 - y0,
                     boxstyle="round,pad=0.2,rounding_size=1",
                     linewidth=0, facecolor=LANE, zorder=0))
        ax.text(1.4, (y0 + y1) / 2, label, rotation=90, va="center", ha="center",
                fontsize=9.5, fontweight="bold", color="#37474f")

    boxes: dict[str, tuple[float, float, float, float]] = {}

    def box(key, x, y, w, h, text, status, fontsize=9):
        edge, fill = status
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                     boxstyle="round,pad=0.3,rounding_size=1.2",
                     linewidth=2, edgecolor=edge, facecolor=fill, zorder=2))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fontsize, color="#1a1a1a", zorder=3, wrap=True)
        boxes[key] = (x, y, w, h)

    def arrow(a, b, side_a="auto", side_b="auto", color="#455a64"):
        ax, ay, aw, ah = boxes[a]
        bx, by, bw, bh = boxes[b]
        ac = (ax + aw / 2, ay + ah / 2)
        bc = (bx + bw / 2, by + bh / 2)

        def anchor(box_, c_other, side):
            x, y, w, h = box_
            cx, cy = x + w / 2, y + h / 2
            if side == "auto":
                side = ("R" if c_other[0] > cx else "L") if abs(c_other[0] - cx) > abs(c_other[1] - cy) else ("T" if c_other[1] > cy else "B")
            return {"R": (x + w, cy), "L": (x, cy), "T": (cx, y + h), "B": (cx, y)}[side]

        pa = anchor((ax, ay, aw, ah), bc, side_a)
        pb = anchor((bx, by, bw, bh), ac, side_b)
        ax_, fig_ = None, None
        arr = FancyArrowPatch(pa, pb, arrowstyle="-|>", mutation_scale=16,
                              linewidth=1.8, color=color, zorder=1,
                              connectionstyle="arc3,rad=0.0")
        plt.gca().add_patch(arr)

    # Mobile lane
    box("M1", 6, 85, 26, 8, "1. Farm onboarding\n(register, crop, sub-plots)", AMBER)
    box("M2", 38, 85, 26, 8, "2. Capture field polygons\n(geometry, on map)", AMBER)
    box("M3", 70, 85, 25, 8, "3. Submit onboarding\nto gateway", AMBER)

    # Gateway lane
    box("G1", 6, 68, 30, 8, "4. Receive + own identity\n& geometry (canonical farm ID)", AMBER)
    box("G2", 40, 68, 28, 8, "5. Distribute farm details\nto analysis apps", AMBER)
    box("G3", 71, 68, 24, 8, "12. Return results\nto farmer & dashboard", AMBER)

    # remote-sense lane
    box("R1", 5, 50, 17, 8, "6. Ingest & validate\n(CRS-UTM, dedup, upsert)", GREEN, 8.3)
    box("R2", 24, 50, 17, 8, "7. Collect Sentinel-2\n(backfill + forward-fill)", GREEN, 8.3)
    box("R3", 43, 50, 17, 8, "8. Analyse indices\nNDVI/EVI2/SAVI/NDRE/NDMI", GREEN, 8.3)
    box("R4", 62, 50, 16, 8, "9. Interpret\n(plain-language + grounding)", AMBER, 8.3)
    box("R5", 80, 50, 15, 8, "10. Agronomist\nreview gate", AMBER, 8.3)
    box("R6", 62, 37, 33, 7.5, "11. Publish / push results\n(GatewayPayload, geometry-free)", GREEN, 8.3)

    # agriAnalysis lane
    box("A1", 8, 22, 22, 7, "Ingest farm details", AMBER)
    box("A2", 38, 22, 22, 7, "Process / analyse", AMBER)
    box("A3", 68, 22, 24, 7, "Produce results", AMBER)

    # consumers
    box("O1", 16, 3, 28, 8, "Farmer\n(AgriTrack mobile app)", AMBER)
    box("O2", 56, 3, 28, 8, "Agronomist dashboard\n(reviewed interpretations)", GREEN)

    # arrows
    arrow("M1", "M2"); arrow("M2", "M3")
    arrow("M3", "G1", "B", "T")
    arrow("G1", "G2");
    arrow("G2", "R1", "B", "T"); arrow("G2", "A1", "B", "T")
    arrow("R1", "R2"); arrow("R2", "R3"); arrow("R3", "R4"); arrow("R4", "R5")
    arrow("R5", "R6", "B", "T")
    arrow("R6", "G3", "T", "B")
    arrow("A1", "A2"); arrow("A2", "A3")
    arrow("A3", "G3", "R", "B")
    arrow("G3", "O1", "B", "T"); arrow("G3", "O2", "B", "T")
    arrow("R5", "O2", "B", "T", color="#9e9e9e")

    # legend
    leg = [("Working / verified", GREEN), ("Built - to verify / pending", AMBER),
           ("Needs attention", RED), ("Not started / unknown", GREY)]
    lx = 6
    for name, (edge, fill) in leg:
        ax.add_patch(FancyBboxPatch((lx, 0.2), 2.2, 2.0,
                     boxstyle="round,pad=0.1,rounding_size=0.4",
                     linewidth=2, edgecolor=edge, facecolor=fill, zorder=2))
        ax.text(lx + 2.6, 1.2, name, va="center", ha="left", fontsize=8.2)
        lx += len(name) * 0.75 + 8

    ax.set_title("Whole-System Major Process Flow  -  AgriTrack -> Gateway -> remote-sense + agriAnalysis -> results",
                 fontsize=13, fontweight="bold", pad=14, color="#1a237e")
    fig.savefig(PNG, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ===========================================================================
# 2. The DOCX
# ===========================================================================
TICK = "☑"   # checked box
BOX = "☐"    # empty box

# (stage, [ (no, step, what, working?, pending?, attention?, notes) ])
# status flags: "Y" puts a checked box in that column; "" leaves it empty
STAGES = [
    ("Stage 1 - Farm onboarding (AgriTrack mobile app)", [
        ("1", "Farmer registration & farm setup", "Farmer creates account, names the farm, adds crop type and sub-plots", "", "Y", "", "Owned by AgriTrack team - confirm live"),
        ("2", "Field polygon capture", "Farmer draws/captures field geometry on the map", "", "Y", "", "Geometry is the canonical input - verify accuracy"),
        ("3", "Submit onboarding to gateway", "Mobile app sends the farm payload to the gateway", "", "Y", "", "POST to gateway onboarding endpoint"),
    ]),
    ("Stage 2 - Gateway distribution (AgriTrack backend)", [
        ("4", "Receive & own identity + geometry", "Gateway stores canonical farm ID + geometry (read-only downstream)", "", "Y", "", "Split-ownership: gateway owns identity/geometry"),
        ("5", "Distribute farm details to analysis apps", "Gateway forwards farm/field/sub-plot to remote-sense and agriAnalysis", "", "Y", "", "remote-sense leg: POST /api/v1/mobile/sync (frozen contract)"),
    ]),
    ("Stage 3 - remote-sense / Sentinel (satellite intelligence)", [
        ("6", "Ingest & validate", "Validate geometry, normalise CRS to UTM, dedup, idempotent upsert", "Y", "", "", "L1 - built & tested (idempotent upsert, race-hardened)"),
        ("7", "Collect Sentinel-2 imagery", "Backfill 18 months + forward-fill on ~5-day cadence via CDSE", "Y", "", "", "L2/L3 - live CDSE verified end-to-end 2026-06-04"),
        ("8", "Analyse spectral indices", "Reflectance, per-AOI SCL mask, NDVI/EVI2/SAVI/NDRE/NDMI, zonal stats, COG", "Y", "", "", "L4 - validation matrix green vs Copernicus (<0.01)"),
        ("9", "Interpret (plain language)", "Claude-API agronomic read grounded in weather + field activity", "Y", "", "", "L4b - built; never auto-publishes"),
        ("10", "Agronomist review gate", "Agronomist reviews/edits the draft before it can publish", "", "Y", "Y", "Threshold sign-off (rs_interpret/thresholds.py) still human-blocked"),
        ("11", "Publish / push results", "Build geometry-free GatewayPayload, push (or gateway pulls) additively", "Y", "", "", "L7 - retry/DLQ/idempotency; geometry never returned"),
        ("11a", "Analyst workspace & dashboard", "Map tiles, timeline, comparison, annotation, overview dashboard", "Y", "", "", "L5/L6 - built, serves & authenticates against BFF"),
    ]),
    ("Stage 4 - agriAnalysis (agronomic analysis web app)", [
        ("A1", "Ingest farm details", "agriAnalysis receives the farm/field payload from the gateway", "", "Y", "", "Separate repo - confirm integration"),
        ("A2", "Process / analyse", "agriAnalysis runs its agronomic processing & analysis", "", "Y", "", "Separate repo - confirm pipeline"),
        ("A3", "Produce results", "agriAnalysis emits results back toward the gateway / dashboard", "", "Y", "", "Separate repo - confirm output contract"),
    ]),
    ("Stage 5 - Results delivery", [
        ("12", "Gateway returns results to farmer", "Gateway delivers combined results to the AgriTrack mobile app", "", "Y", "", "Same payload pushed or pulled (GET /api/v1/mobile/data)"),
        ("13", "Farmer sees results in mobile app", "Farmer views health/indices/notes in AgriTrack", "", "Y", "", "End-to-end check from a real onboarded farm"),
        ("14", "Agronomist dashboard shows results", "Reviewed interpretations surface on the agronomist dashboard", "Y", "", "", "remote-sense workspace built; verify cross-app view"),
    ]),
]


def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def set_cell_text(cell, text, bold=False, size=9, color=None, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    if align:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def build_docx() -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    # Title
    h = doc.add_heading("Whole-System Progress Tracker", level=0)
    sub = doc.add_paragraph()
    r = sub.add_run("AgriTrack mobile app  ->  Gateway  ->  remote-sense (Sentinel) + agriAnalysis  ->  results to farmer & agronomist")
    r.italic = True
    r.font.size = Pt(11)
    meta = doc.add_paragraph()
    mr = meta.add_run("Major process flows only. Tick each step Working / Pending / Needs attention as you verify it.  Generated 2026-06-19.")
    mr.font.size = Pt(9)
    mr.font.color.rgb = RGBColor.from_string("616161")

    # Diagram
    doc.add_heading("1. System flow diagram", level=1)
    doc.add_picture(PNG, width=Inches(6.6))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Legend / how to use
    doc.add_heading("2. How to use this tracker", level=1)
    for line in [
        "Each row is a major process. Use the three status columns to mark where it stands.",
        f"{TICK} = mark this box (double-click in Word, or replace {BOX} with {TICK}).",
        "Working / verified  -  you have seen it run correctly end to end.",
        "Pending  -  built but not yet verified, or waiting on another team / a decision.",
        "Needs attention  -  blocked, failing, or a known gap.",
        "Pre-ticked rows reflect evidence in the remote-sense repo; everything in the mobile app, gateway, and agriAnalysis is marked Pending because it lives in other repos - confirm and re-tick.",
    ]:
        p = doc.add_paragraph(line, style="List Bullet")
        p.runs[0].font.size = Pt(9.5)

    # Checklist
    doc.add_heading("3. Progress checklist", level=1)
    headers = ["#", "Process step", "What it does", "Working", "Pending", "Needs\nattention", "Notes / evidence"]
    widths = [0.35, 1.55, 2.2, 0.6, 0.6, 0.7, 2.1]

    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, htext in enumerate(headers):
        set_cell_text(hdr[i], htext, bold=True, size=9, color="FFFFFF",
                      align=WD_ALIGN_PARAGRAPH.CENTER)
        shade(hdr[i], "1a237e")

    for stage_name, rows in STAGES:
        srow = table.add_row().cells
        a = srow[0]
        a.merge(srow[-1])
        set_cell_text(a, stage_name, bold=True, size=10, color="0d47a1")
        shade(a, "dce6f5")
        for no, step, what, w, pend, att, notes in rows:
            c = table.add_row().cells
            set_cell_text(c[0], no, size=8.5, align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(c[1], step, bold=True, size=9)
            set_cell_text(c[2], what, size=8.5)
            set_cell_text(c[3], TICK if w else BOX, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(c[4], TICK if pend else BOX, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(c[5], TICK if att else BOX, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(c[6], notes, size=8, color="424242")
            if w:
                shade(c[3], "c8e6c9")
            if att:
                shade(c[5], "ffcdd2")
            elif pend:
                shade(c[4], "fff3c4")

    # column widths
    for row in table.rows:
        for i, wdt in enumerate(widths):
            row.cells[i].width = Inches(wdt)

    # Assumptions
    doc.add_heading("4. Scope & assumptions", level=1)
    for line in [
        "This tracker is generated from the remote-sense repository. remote-sense is the satellite-intelligence web app (referred to here as \"Sentinel\"); its status is evidence-backed (functionally complete, validation matrix green, live CDSE verified).",
        "The AgriTrack mobile app, the gateway, and agriAnalysis live in other repositories and cannot be verified from here, so their steps default to Pending - confirm them against those codebases and re-tick.",
        "The external contract between the gateway and remote-sense is frozen: POST /api/v1/mobile/sync (inbound onboarding), GET /api/v1/mobile/data (results pull), and the outbound GatewayPayload push. Geometry is never returned downstream.",
        "Known open item inside remote-sense: agronomist sign-off on interpretation thresholds (rs_interpret/thresholds.py) - flagged Needs attention at step 10.",
    ]:
        p = doc.add_paragraph(line, style="List Bullet")
        p.runs[0].font.size = Pt(9.5)

    doc.save(DOCX)


if __name__ == "__main__":
    draw_diagram()
    build_docx()
    print("Wrote:", DOCX)
