from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.utils import ImageReader


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "deliverables" / "Sentinel_Camera_AI_Built_System_Guide.pdf"

NAVY = colors.HexColor("#0B1F3A")
BLUE = colors.HexColor("#1261A0")
CYAN = colors.HexColor("#00A7E1")
GREEN = colors.HexColor("#16865C")
AMBER = colors.HexColor("#D68A00")
RED = colors.HexColor("#B33A3A")
INK = colors.HexColor("#1B2635")
MUTED = colors.HexColor("#536273")
LIGHT = colors.HexColor("#EDF3F8")
PALE_BLUE = colors.HexColor("#E8F4FB")
PALE_GREEN = colors.HexColor("#EAF6F1")
PALE_AMBER = colors.HexColor("#FFF6DE")
GRID = colors.HexColor("#C9D5E1")


def register_fonts() -> None:
    pdfmetrics.registerFont(
        TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    pdfmetrics.registerFont(
        TTFont(
            "DejaVu-Bold",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
    )
    pdfmetrics.registerFont(
        TTFont(
            "DejaVu-Mono",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        )
    )


register_fonts()
BASE = getSampleStyleSheet()
STYLES = {
    "title": ParagraphStyle(
        "Title",
        parent=BASE["Title"],
        fontName="DejaVu-Bold",
        fontSize=26,
        leading=32,
        textColor=colors.white,
        alignment=TA_LEFT,
        spaceAfter=7 * mm,
    ),
    "subtitle": ParagraphStyle(
        "Subtitle",
        parent=BASE["Normal"],
        fontName="DejaVu",
        fontSize=13,
        leading=19,
        textColor=colors.HexColor("#D8EAF7"),
    ),
    "h1": ParagraphStyle(
        "H1",
        parent=BASE["Heading1"],
        fontName="DejaVu-Bold",
        fontSize=18,
        leading=23,
        textColor=NAVY,
        spaceBefore=3 * mm,
        spaceAfter=4 * mm,
        keepWithNext=True,
    ),
    "h2": ParagraphStyle(
        "H2",
        parent=BASE["Heading2"],
        fontName="DejaVu-Bold",
        fontSize=13.2,
        leading=17,
        textColor=BLUE,
        spaceBefore=4 * mm,
        spaceAfter=2.2 * mm,
        keepWithNext=True,
    ),
    "h3": ParagraphStyle(
        "H3",
        parent=BASE["Heading3"],
        fontName="DejaVu-Bold",
        fontSize=11.2,
        leading=14,
        textColor=INK,
        spaceBefore=3 * mm,
        spaceAfter=1.5 * mm,
        keepWithNext=True,
    ),
    "body": ParagraphStyle(
        "Body",
        parent=BASE["BodyText"],
        fontName="DejaVu",
        fontSize=10.3,
        leading=15.2,
        textColor=INK,
        spaceAfter=2.5 * mm,
    ),
    "small": ParagraphStyle(
        "Small",
        parent=BASE["BodyText"],
        fontName="DejaVu",
        fontSize=8.6,
        leading=12,
        textColor=MUTED,
        spaceAfter=1.8 * mm,
    ),
    "caption": ParagraphStyle(
        "Caption",
        parent=BASE["BodyText"],
        fontName="DejaVu",
        fontSize=8.2,
        leading=11,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceBefore=1.4 * mm,
        spaceAfter=2.5 * mm,
    ),
    "code": ParagraphStyle(
        "Code",
        parent=BASE["Code"],
        fontName="DejaVu-Mono",
        fontSize=8.1,
        leading=11.1,
        leftIndent=4 * mm,
        rightIndent=4 * mm,
        borderWidth=0.6,
        borderColor=GRID,
        borderPadding=3 * mm,
        backColor=colors.HexColor("#F7F9FB"),
        textColor=colors.HexColor("#16202B"),
        spaceBefore=1.5 * mm,
        spaceAfter=3 * mm,
    ),
    "table_header": ParagraphStyle(
        "TableHeader",
        parent=BASE["BodyText"],
        fontName="DejaVu-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    ),
    "table": ParagraphStyle(
        "Table",
        parent=BASE["BodyText"],
        fontName="DejaVu",
        fontSize=8.5,
        leading=11.5,
        textColor=INK,
    ),
    "callout": ParagraphStyle(
        "Callout",
        parent=BASE["BodyText"],
        fontName="DejaVu",
        fontSize=10,
        leading=14.5,
        textColor=INK,
    ),
    "cover_label": ParagraphStyle(
        "CoverLabel",
        parent=BASE["Normal"],
        fontName="DejaVu-Bold",
        fontSize=9,
        leading=12,
        textColor=CYAN,
        spaceAfter=3 * mm,
    ),
}


def para(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def bullets(items: list[str], level: int = 0) -> ListFlowable:
    return ListFlowable(
        [ListItem(para(item), leftIndent=5 * mm) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=(5 + level * 4) * mm,
        bulletFontName="DejaVu",
        bulletFontSize=7,
        bulletColor=BLUE,
        spaceAfter=2 * mm,
    )


def numbered(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(para(item), leftIndent=7 * mm) for item in items],
        bulletType="1",
        leftIndent=7 * mm,
        bulletFontName="DejaVu-Bold",
        bulletFontSize=9,
        bulletColor=BLUE,
        spaceAfter=2 * mm,
    )


def code(text: str) -> Preformatted:
    return Preformatted(text.strip(), STYLES["code"])


def callout(title: str, text: str, kind: str = "info") -> Table:
    palette = {
        "info": (BLUE, PALE_BLUE),
        "good": (GREEN, PALE_GREEN),
        "warn": (AMBER, PALE_AMBER),
        "danger": (RED, colors.HexColor("#FBECEC")),
    }
    accent, background = palette[kind]
    content = para(f"<b>{title}</b><br/>{text}", "callout")
    table = Table([[content]], colWidths=[170 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.7, accent),
                ("LINEBEFORE", (0, 0), (0, -1), 3.2, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5 * mm),
            ]
        )
    )
    return table


def data_table(
    headers: list[str],
    rows: list[list[str]],
    widths: list[float],
) -> Table:
    data = [[para(header, "table_header") for header in headers]]
    data.extend([[para(cell, "table") for cell in row] for row in rows])
    table = Table(data, colWidths=[value * mm for value in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.45, GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2.5 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2.5 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ]
        )
    )
    return table


def fit_image(path: Path, max_width_mm: float, max_height_mm: float) -> Image:
    reader = ImageReader(str(path))
    width, height = reader.getSize()
    scale = min((max_width_mm * mm) / width, (max_height_mm * mm) / height)
    return Image(str(path), width=width * scale, height=height * scale)


def architecture_table() -> Table:
    rows = [
        [
            para("<b>1. Media input</b><br/>Image, MP4, AVI, MOV", "table"),
            para("<b>2. Adaptive mode</b><br/>NORMAL or HEIGHTENED", "table"),
        ],
        [
            para(
                "<b>3. Perception</b><br/>Person/vehicle • face • plate • OCR",
                "table",
            ),
            para(
                "<b>4. Evidence quality</b><br/>Trust score • confidence • fallbacks",
                "table",
            ),
        ],
        [
            para(
                "<b>5. Event builder</b><br/>Crops • annotation • schema v1 JSON",
                "table",
            ),
            para(
                "<b>6. Match/publish</b><br/>Vehicle • face • appearance • AWS",
                "table",
            ),
        ],
    ]
    table = Table(rows, colWidths=[83 * mm, 83 * mm], rowHeights=[25 * mm] * 3)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, BLUE),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [PALE_BLUE, colors.white]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
            ]
        )
    )
    return table


def page_frame(canvas, doc) -> None:
    page = canvas.getPageNumber()
    width, height = A4
    canvas.saveState()
    if page == 1:
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, width, height, fill=1, stroke=0)
        canvas.setFillColor(CYAN)
        canvas.rect(0, height - 13 * mm, width, 13 * mm, fill=1, stroke=0)
        canvas.restoreState()
        return
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 10 * mm, width, 10 * mm, fill=1, stroke=0)
    canvas.setFillColor(CYAN)
    canvas.rect(0, height - 11.3 * mm, width, 1.3 * mm, fill=1, stroke=0)
    canvas.setFont("DejaVu-Bold", 7.8)
    canvas.setFillColor(colors.white)
    canvas.drawString(20 * mm, height - 6.8 * mm, "SENTINEL MESH  •  CAMERA AI")
    canvas.setFont("DejaVu", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 9 * mm, "Built System, Setup & AWS Deployment Guide")
    canvas.drawRightString(width - 20 * mm, 9 * mm, f"Page {page}")
    canvas.setStrokeColor(GRID)
    canvas.line(20 * mm, 13 * mm, width - 20 * mm, 13 * mm)
    canvas.restoreState()


def build_story() -> list:
    story: list = []

    story.extend(
        [
            Spacer(1, 37 * mm),
            para("DISCOVERY SENTINEL MESH", "cover_label"),
            para("Camera AI: Built System, Setup & AWS Deployment Guide", "title"),
            para(
                "A complete local-first implementation for face presence, licence-plate "
                "detection and OCR, vehicle attributes, repeat matching, Camera Trust "
                "Score, adaptive processing modes, evidence JSON and staged AWS publishing.",
                "subtitle",
            ),
            Spacer(1, 18 * mm),
            Table(
                [
                    [
                        para(
                            "<b>BUILD STATUS</b><br/>16 automated tests passing",
                            "callout",
                        ),
                        para(
                            "<b>CONTROLLED DEMO</b><br/>Vehicle and face paths verified",
                            "callout",
                        ),
                    ],
                    [
                        para(
                            "<b>PRIMARY PLATFORM</b><br/>Windows + Python 3.11",
                            "callout",
                        ),
                        para(
                            "<b>CLOUD STRATEGY</b><br/>Local first, private AWS second",
                            "callout",
                        ),
                    ],
                ],
                colWidths=[82 * mm, 82 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#102D50")),
                        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#31587A")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
                        ("TOPPADDING", (0, 0), (-1, -1), 5 * mm),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5 * mm),
                    ]
                ),
            ),
            Spacer(1, 31 * mm),
            para(
                "Prepared 25 July 2026  •  Africa/Johannesburg  •  Project v0.1.0",
                "subtitle",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("How to use this guide", "h1"),
            callout(
                "Best route for a beginner",
                "Get the included synthetic demo working locally first. Only after every "
                "event JSON and evidence crop is visible should you enable AWS. This keeps "
                "computer-vision debugging separate from cloud permissions and networking.",
                "good",
            ),
            Spacer(1, 4 * mm),
            data_table(
                ["Part", "Use it for"],
                [
                    ["1–3", "Understand what is built and run the first Windows demo."],
                    ["4–7", "Explain each model, the JSON contract, matching and trust score."],
                    ["8–10", "Connect private AWS storage and later container processing."],
                    ["11–14", "Tune real footage, add height/accessories, troubleshoot and present."],
                ],
                [25, 145],
            ),
            para("The shortest successful path", "h2"),
            numbered(
                [
                    "Extract the project ZIP into a short path such as "
                    "<font name='DejaVu-Mono'>C:\\sentinel-camera-ai</font>.",
                    "Install Python 3.11 and Tesseract 5.",
                    "Run <font name='DejaVu-Mono'>scripts\\setup_windows.ps1</font>.",
                    "Run <font name='DejaVu-Mono'>scripts\\run_demo.ps1</font>.",
                    "Open the generated annotated images and event JSON under "
                    "<font name='DejaVu-Mono'>output</font>.",
                    "Replace synthetic media with short, authorized clips and tune confidence thresholds.",
                    "Configure AWS IAM Identity Center and a private S3 bucket; publish only after local success.",
                ]
            ),
            callout(
                "Accuracy promise",
                "No camera AI can be guaranteed never to fail. This build is engineered "
                "to fail visibly: uncertain results become Unknown/null, fallbacks are "
                "recorded, and cross-camera links remain evidence-ranked candidates.",
                "warn",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("1. What has been built", "h1"),
            para(
                "The project is a modular Python package and command-line application. It "
                "accepts an image or recorded clip and writes a versioned event JSON plus "
                "annotated evidence, face crops, plate crops and optional anonymous face vectors.",
            ),
            data_table(
                ["Capability", "Implemented now", "What the result means"],
                [
                    [
                        "Face presence",
                        "YuNet primary; Haar fallback; boxes, crops, confidence",
                        "Detects a face. It does not name or identify a person.",
                    ],
                    [
                        "Face comparison",
                        "SFace anonymous embedding and cosine similarity",
                        "Possible same face with evidence strength; not an identity probability.",
                    ],
                    [
                        "Plate",
                        "YOLO adapter, LPD-YuNet/contour fallback, PaddleOCR/Tesseract",
                        "Separate detection and OCR confidence; normalized SA-style suffix display.",
                    ],
                    [
                        "Vehicle",
                        "Broad type, dominant colour, movement direction",
                        "Make/model remains null unless a reliable future model supplies it.",
                    ],
                    [
                        "Appearance",
                        "Upper and lower colour; cap/backpack schema fields",
                        "Colours work now. Cap/backpack stay null until an attribute model is added.",
                    ],
                    [
                        "Repeat pass",
                        "Gap-aware in-clip event buckets and cross-event comparison",
                        "A return after the cooldown can become a new pass event.",
                    ],
                    [
                        "Trust score",
                        "Blur, lighting, detections, obstruction and resolution",
                        "Quality heuristic out of 100, with readable reasons.",
                    ],
                    [
                        "Adaptive edge",
                        "NORMAL and HEIGHTENED modes",
                        "The system spends more compute only when risk is elevated.",
                    ],
                    [
                        "AWS",
                        "Private S3 + optional ingestion API publisher",
                        "Built and unit-tested without mutating a live AWS account.",
                    ],
                ],
                [32, 67, 71],
            ),
            para("Project layout", "h2"),
            code(
                r"""
sentinel-camera-ai/
  config/default.yaml       # thresholds, modes, AWS settings
  media/                    # safe synthetic clips and face fixture
  models/                   # official OpenCV models + licences
  schemas/                  # JSON Schemas
  scripts/                  # Windows setup, demo, model download
  src/sentinel_camera_ai/   # pipeline, detectors, matching, AWS
  tests/                    # unit and end-to-end tests
  demo-output/              # verified example evidence
"""
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("2. Architecture and data flow", "h1"),
            architecture_table(),
            Spacer(1, 5 * mm),
            para("Why this structure is reliable", "h2"),
            bullets(
                [
                    "<b>Adapters, not one giant script:</b> detectors can be replaced without changing the event contract.",
                    "<b>Primary + fallback:</b> optional model failures do not silently destroy the controlled demo.",
                    "<b>One event schema:</b> the team receives the same shape whether input came from an image, clip or future webcam.",
                    "<b>Evidence first:</b> every decision can link back to a crop, annotated frame, confidence and model name.",
                    "<b>Abstention:</b> missing or weak evidence becomes Unknown/null instead of an invented answer.",
                ]
            ),
            para("Runtime sequence", "h2"),
            numbered(
                [
                    "Open media and sample frames according to the selected mode.",
                    "Optionally gate quiet frames using motion.",
                    "Detect people/vehicles, faces and plate candidates.",
                    "Run heavy OCR only at the configured interval.",
                    "Extract vehicle colour, direction and visible appearance colours.",
                    "Calculate the Camera Trust Score.",
                    "Merge continuous observations; split a return after the cooldown.",
                    "Keep the highest-quality representative frame for each event.",
                    "Write evidence and schema-v1 JSON; optionally publish to AWS.",
                ]
            ),
            para(
                "<b>Distinctive design:</b> Sentinel Mesh decides when to spend compute, "
                "rates evidence quality, and records why multi-signal links were made.",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("3. Windows installation and first run", "h1"),
            para("Prerequisites", "h2"),
            data_table(
                ["Item", "Recommended", "Why"],
                [
                    ["Python", "3.11 x64", "Supported by the package and common CV wheels."],
                    ["Tesseract", "5.x on PATH", "Offline OCR fallback."],
                    ["Git", "Current stable", "Version control and collaboration."],
                    ["FFmpeg", "Current stable", "Useful for inspecting/converting clips."],
                    ["RAM", "8 GB minimum; 16 GB preferable", "Model loading and video frames."],
                    ["GPU", "Optional", "CPU is enough for the controlled demo."],
                ],
                [32, 48, 90],
            ),
            para("One-command project setup", "h2"),
            code(
                r"""
cd C:\sentinel-camera-ai
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_windows.ps1
"""
            ),
            para(
                "The setup script creates <font name='DejaVu-Mono'>.venv</font>, installs "
                "the package, AWS/YOLO/development extras, downloads official OpenCV Zoo "
                "models and runs the dependency doctor.",
            ),
            para("If Tesseract is not on PATH", "h2"),
            code(
                r"""
$env:TESSERACT_CMD =
  "C:\Program Files\Tesseract-OCR\tesseract.exe"
"""
            ),
            para("Run the controlled demo", "h2"),
            code(r""".\scripts\run_demo.ps1"""),
            para("Expected result", "h2"),
            bullets(
                [
                    "Two clip events containing plate text <b>AB12CDGP</b>.",
                    "Vehicle evidence <b>Blue / Car / right</b>.",
                    "Camera Trust Scores around the high 70s for selected frames.",
                    "A comparison JSON marking possible same vehicle as true with HIGH evidence.",
                ]
            ),
            callout(
                "If setup stops",
                "Do not skip the error. Run "
                "<font name='DejaVu-Mono'>python -m sentinel_camera_ai "
                "--config config\\default.yaml doctor</font> and use the troubleshooting "
                "section later in this guide.",
                "warn",
            ),
            PageBreak(),
        ]
    )

    vehicle_image = (
        ROOT / "demo-output/evidence/EVT-4ADFB703C2/annotated_frame.jpg"
    )
    face_image = (
        ROOT
        / "demo-output/face-verified/evidence/EVT-E1A0975120/annotated_frame.jpg"
    )
    story.extend(
        [
            para("4. What the verified demo produces", "h1"),
            para(
                "These are actual outputs from the packaged build. The media is synthetic, "
                "so the team can demonstrate the pipeline without exposing a real face or plate.",
            ),
            fit_image(vehicle_image, 168, 80),
            para(
                "Vehicle event: car 0.68, plate candidate 0.82, OCR AB12CDGP, "
                "Blue / Car / right, Camera Trust 79/100.",
                "caption",
            ),
            fit_image(face_image, 110, 108),
            para(
                "Fictional face fixture: one YuNet face at approximately 0.895 confidence. "
                "The crop and SFace embedding were saved; no unreadable plate candidate was retained.",
                "caption",
            ),
            para(
                "Inspect <font name='DejaVu-Mono'>demo-output/</font> for both event JSON "
                "files, the vehicle comparison, the face comparison and all evidence crops.",
                "small",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("5. Detector and OCR strategy", "h1"),
            para("Person and vehicle", "h2"),
            bullets(
                [
                    "Preferred: Ultralytics-compatible YOLO for person, car, motorcycle, bus and truck.",
                    "Fallback: an OpenCV colour/contour heuristic used only to keep the synthetic integration fixture self-contained.",
                    "Do not judge real CCTV accuracy using the heuristic fallback.",
                ]
            ),
            para("Face", "h2"),
            bullets(
                [
                    "OpenCV YuNet supplies face boxes, five facial landmarks and detection confidence.",
                    "OpenCV Haar is the last-resort detector when YuNet cannot load.",
                    "OpenCV SFace aligns the detected face and produces an anonymous numeric vector.",
                    "The JSON stores an embedding file reference, not a person's name.",
                ]
            ),
            para("Licence plate", "h2"),
            numbered(
                [
                    "Use <font name='DejaVu-Mono'>models/license_plate_detector.pt</font> when a tested, properly licensed plate-specific YOLO model is available.",
                    "Otherwise combine OpenCV LPD-YuNet proposals with contour proposals.",
                    "Reject impossible plate aspect ratios and oversized candidates.",
                    "Crop the two best candidates and run OCR preprocessing variants.",
                    "Use PaddleOCR when installed; fall back to local Tesseract.",
                    "Normalize to uppercase alphanumeric text and keep detection/OCR confidence separate.",
                ]
            ),
            callout(
                "South African limitation",
                "The included synthetic plate demonstrates the required SA-style suffix "
                "<b>GP</b>. The bundled LPD-YuNet detector was not trained for South African "
                "plates. Before real evaluation, add a geographically suitable plate model "
                "and test day/night/angle cases on authorized local examples.",
                "warn",
            ),
            para("How to add a stronger plate model", "h2"),
            code(
                r"""
# Place an Ultralytics-compatible model here:
models\license_plate_detector.pt

# Keep config/default.yaml:
plate:
  backend: auto
  yolo_model: models/license_plate_detector.pt
"""
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("6. Event JSON contract", "h1"),
            para(
                "Every event is validated by Pydantic and can also be checked against "
                "<font name='DejaVu-Mono'>schemas/event-v1.schema.json</font>. Downstream "
                "teammates should integrate against this contract, not detector internals.",
            ),
            data_table(
                ["JSON area", "Important fields", "Integration meaning"],
                [
                    ["Identity", "schema_version, event_id, camera_id", "Stable routing and version handling."],
                    ["Time/place", "timezone-aware timestamp, latitude, longitude", "Map and sequence events safely."],
                    ["Face", "present, count, boxes, crops, embedding_ref, confidence", "Presence and optional anonymous comparison."],
                    ["Plate", "text, display_text, box, crop, detection/OCR confidence", "Searchable vehicle evidence with uncertainty."],
                    ["Vehicle", "colour, type, make_model, direction, box", "Broad attributes; no invented make/model."],
                    ["Appearance", "upper/lower colour, cap, backpack, person_box", "Visible, non-identity appearance cues."],
                    ["Quality", "camera_trust_score, metrics, reasons", "Whether evidence is suitable for linking/review."],
                    ["Audit", "model_versions, metadata, fallback notes", "Reproduce and explain the event."],
                ],
                [31, 69, 70],
            ),
            para("Example core output", "h2"),
            code(
                r"""
{
  "event_id": "EVT-4ADFB703C2",
  "camera_id": "CAM01",
  "timestamp": "2026-07-24T21:07:04+02:00",
  "mode": "HEIGHTENED",
  "face": {"present": false, "count": 0},
  "plate": {
    "text": "AB12CDGP",
    "detection_confidence": 0.82,
    "ocr_confidence": 0.72
  },
  "vehicle": {
    "colour": "Blue", "type": "Car", "direction": "right"
  },
  "camera_trust_score": 79
}
"""
            ),
            para("Validate an event", "h2"),
            code(
                r"""
python -m sentinel_camera_ai --config config\default.yaml `
  validate --event output\events\EVT-....json
"""
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("7. Matching, Camera Trust and adaptive modes", "h1"),
            para("Cross-event matching", "h2"),
            data_table(
                ["Decision", "Signals", "Current rule"],
                [
                    ["Possible same vehicle", "Plate 65%, colour 20%, broad type 15%", "True at score ≥ 0.76 when both plates exist."],
                    ["Possible same face", "SFace cosine similarity + minimum camera trust", "True at similarity ≥ 0.78 and trust ≥ 55."],
                    ["Possible same appearance", "Upper 45%, lower 35%, optional cap/backpack", "True at score ≥ 0.75 with real appearance evidence."],
                ],
                [42, 69, 59],
            ),
            para(
                "Missing plate or face evidence does not become a false match. The comparison "
                "returns NONE/LOW evidence and a warning where appropriate.",
            ),
            para("Camera Trust Score", "h2"),
            data_table(
                ["Component", "Weight", "Purpose"],
                [
                    ["Sharpness", "25%", "Penalizes blur using Laplacian variance."],
                    ["Lighting", "20%", "Rewards usable brightness; penalizes very dark/bright frames."],
                    ["Detection", "30%", "Aggregates current detector/OCR confidence."],
                    ["Unobstructed", "15%", "Penalizes evidence touching/cut by frame boundaries."],
                    ["Resolution", "10%", "Rewards sufficient evidence pixels."],
                ],
                [45, 25, 100],
            ),
            callout(
                "Interpretation",
                "78/100 means the selected frame is reasonably usable under the configured "
                "heuristic. It is not a probability that the plate or person is correct.",
                "info",
            ),
            para("Adaptive processing", "h2"),
            data_table(
                ["Mode", "Runs", "Use"],
                [
                    ["NORMAL", "Sparse frames, basic person/vehicle; face/plate/appearance off", "Routine low-risk monitoring."],
                    ["HEIGHTENED", "Dense frames, face, plate/OCR, vehicle and appearance", "Alert window or high-risk time."],
                ],
                [34, 80, 56],
            ),
            para(
                "Continuous detections merge while the observation gap is within "
                "<font name='DejaVu-Mono'>dedupe_cooldown_seconds</font>. If the object "
                "disappears longer than the cooldown and returns, a new pass event can be created.",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("8. AWS: the recommended rollout", "h1"),
            callout(
                "Recommended cloud boundary",
                "Run inference locally for the first demo. Use AWS first for private evidence "
                "storage and event sharing. Move video inference into containers only after "
                "accuracy and runtime are measured on representative clips.",
                "good",
            ),
            para("Stage 1 — private evidence storage", "h2"),
            numbered(
                [
                    "Create one private S3 bucket in <b>af-south-1</b> or the team's chosen Region.",
                    "Enable default encryption and block all public access.",
                    "Use IAM Identity Center for developers instead of long-lived access keys.",
                    "Give the processing role only the required object prefixes.",
                    "Configure <font name='DejaVu-Mono'>bucket</font>, <font name='DejaVu-Mono'>region</font> and <font name='DejaVu-Mono'>aws.enabled</font>.",
                    "Run a single synthetic event with <font name='DejaVu-Mono'>--publish-aws</font>.",
                    "Confirm evidence and event JSON are private before using real authorized media.",
                ]
            ),
            para("Suggested object layout", "h2"),
            code(
                r"""
s3://sentinel-mesh-dev/
  incoming/CAM01/<clip-id>.mp4
  evidence/<event-id>/annotated_frame.jpg
  evidence/<event-id>/plate.jpg
  evidence/<event-id>/faces/face_0.jpg
  events/YYYY/MM/DD/<event-id>.json
"""
            ),
            para("Stage 2 — team API", "h2"),
            para(
                "Set <font name='DejaVu-Mono'>aws.ingestion_url</font> only when the backend "
                "has an authenticated HTTPS endpoint. Store the API token outside the repository "
                "in <font name='DejaVu-Mono'>SENTINEL_API_TOKEN</font> or a managed secret.",
            ),
            para("Stage 3 — managed batch processing", "h2"),
            para(
                "For longer clips, package this project as a container, push to ECR, run it as "
                "an ECS/Fargate task and orchestrate it with Step Functions. This avoids forcing "
                "large models and long video jobs into a short serverless function.",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("9. AWS beginner setup", "h1"),
            para("Configure identity", "h2"),
            code(
                r"""
aws configure sso
aws sso login --profile sentinel-dev
aws sts get-caller-identity --profile sentinel-dev
"""
            ),
            para("Configure the application", "h2"),
            code(
                r"""
# config/default.yaml
aws:
  enabled: true
  profile: sentinel-dev
  region: af-south-1
  bucket: your-private-sentinel-bucket
  evidence_prefix: evidence
  events_prefix: events
  ingestion_url: ""
"""
            ),
            para("Publish one test", "h2"),
            code(
                r"""
python -m sentinel_camera_ai --config config\default.yaml process `
  --input media\camera_1_clip.mp4 `
  --camera-id CAM01 `
  --mode HEIGHTENED `
  --timestamp "2026-07-24T21:07:00+02:00" `
  --publish-aws
"""
            ),
            para("Minimum role actions", "h2"),
            bullets(
                [
                    "<font name='DejaVu-Mono'>s3:PutObject</font> on only the evidence/events prefixes.",
                    "<font name='DejaVu-Mono'>s3:GetObject</font> only if this worker must read incoming clips.",
                    "<font name='DejaVu-Mono'>s3:ListBucket</font> only with a prefix condition if listing is needed.",
                    "KMS permissions only when using a customer-managed encryption key.",
                ]
            ),
            callout(
                "Do not do this",
                "Do not paste AWS access keys into YAML, code, screenshots or chat. Do not make "
                "the evidence bucket public. Use short-lived SSO credentials locally and an IAM "
                "task role in ECS.",
                "danger",
            ),
            para("Prevent S3 trigger loops", "h2"),
            para(
                "If a future S3 upload triggers processing, separate "
                "<font name='DejaVu-Mono'>incoming/</font> from "
                "<font name='DejaVu-Mono'>evidence/</font>, and filter the event to incoming "
                "media suffixes. Otherwise the worker's own output upload can trigger another run.",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("10. Cloud architecture after the local demo", "h1"),
            data_table(
                ["AWS service", "Recommended responsibility", "Start when"],
                [
                    ["S3", "Private incoming clips, evidence and event JSON", "Immediately after local success."],
                    ["IAM Identity Center", "Short-lived developer access", "Before the first AWS test."],
                    ["ECR", "Store the built camera-AI container", "When deploying managed inference."],
                    ["ECS/Fargate", "Run video-processing tasks", "When runtime/accuracy is measured."],
                    ["Step Functions", "Start task, wait, retry and record status", "When more than one processing step exists."],
                    ["DynamoDB", "Index event metadata and repeat sightings", "When the UI needs fast event queries."],
                    ["API Gateway + backend", "Authenticated team ingestion/query API", "When frontend integration is ready."],
                    ["CloudWatch", "Logs, failure alarms and processing metrics", "With the first deployed worker."],
                    ["Secrets Manager", "Store external API secret if required", "Only if an external token is used."],
                ],
                [35, 88, 47],
            ),
            para("Suggested event sequence", "h2"),
            numbered(
                [
                    "Authorized clip arrives under S3 incoming.",
                    "A small ingestion component records a job and starts a Step Functions execution.",
                    "Step Functions launches an ECS/Fargate task with the S3 input URI and camera metadata.",
                    "The container downloads the clip, runs this package, and uploads evidence/events.",
                    "The backend indexes event metadata and creates temporary presigned evidence links for authenticated users.",
                    "CloudWatch records duration, failures, low-trust rates and model versions.",
                ]
            ),
            para("Why not start with Lambda video inference?", "h2"),
            para(
                "Short metadata handlers are a good Lambda fit. Full video inference brings "
                "large images, models, native libraries and variable runtime. The included "
                "container is therefore positioned for Fargate first; Lambda can remain the "
                "lightweight trigger or validator.",
            ),
            callout(
                "Cost control",
                "Keep NORMAL mode local, upload event evidence instead of continuous raw video, "
                "apply S3 lifecycle rules, cap clip duration, and run Fargate only for queued work.",
                "good",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("11. Real-footage accuracy plan", "h1"),
            para(
                "The correct next step is not more features. It is a small labelled validation "
                "set that reflects the actual cameras.",
            ),
            para("Build a consented validation pack", "h2"),
            bullets(
                [
                    "Day, dusk and night clips from each planned camera angle.",
                    "Near/far vehicles, front/rear plates, blur and partial obstruction.",
                    "Authorized South African plate formats relevant to the demo area.",
                    "Consented same-person and different-person pairs; include caps and changed clothing.",
                    "Negative images containing signs, logos and face-like/plate-like patterns.",
                ]
            ),
            para("Measure the right things", "h2"),
            data_table(
                ["Area", "Measure", "Do not accept"],
                [
                    ["Plate detection", "Precision/recall at IoU ≥ 0.5", "Only screenshots of best cases."],
                    ["Plate OCR", "Full-plate exact match and character error rate", "Substring success counted as perfect."],
                    ["Face presence", "Precision/recall by face size and lighting", "Identity claims from detection confidence."],
                    ["Repeat match", "Same/different pair ROC, false-match rate", "One universal threshold without calibration."],
                    ["Trust score", "Correlation with OCR/detection failures", "Treating 78 as a probability."],
                    ["Runtime", "Frames/sec, peak RAM, event latency", "Testing only on a developer desktop."],
                ],
                [37, 72, 61],
            ),
            para("Threshold tuning order", "h2"),
            numbered(
                [
                    "Tune object/face/plate detection confidence for false positives.",
                    "Tune OCR acceptance for full-plate accuracy.",
                    "Tune event stability and cooldown for duplicate vs repeat-pass behavior.",
                    "Tune matching thresholds on same/different pairs.",
                    "Recalibrate Camera Trust components against actual failure rates.",
                    "Freeze config and model checksums before the final demonstration.",
                ]
            ),
            callout(
                "Human review rule",
                "Any low-trust, low-evidence or consequential cross-camera match should be "
                "presented as a candidate with source frames—not as an automatic identity conclusion.",
                "warn",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("12. Height, caps and richer points of interest", "h1"),
            callout(
                "Current status",
                "Upper/lower clothing colours are implemented. Cap and backpack fields exist "
                "but remain null. Absolute height estimation is intentionally not claimed yet.",
                "info",
            ),
            para("Height estimation done correctly", "h2"),
            para(
                "Pixel height alone is not real height: a nearby short person can occupy more "
                "pixels than a distant tall person. For a fixed camera, calibrate the ground "
                "plane and camera geometry.",
            ),
            numbered(
                [
                    "Keep the camera fixed; record resolution, mounting height, tilt and lens.",
                    "Mark at least four known points on the walking plane and compute a homography.",
                    "Use a person detector plus pose/keypoints to estimate head and foot points.",
                    "Project the foot point onto the calibrated ground plane.",
                    "Use the vertical camera geometry or a known-height reference to estimate height.",
                    "Reject crouching, sitting, occluded or foot-out-of-frame cases.",
                    "Return a range such as 1.70–1.80 m with calibration confidence, never a false exact value.",
                ]
            ),
            para("Caps, backpacks and memorable appearance", "h2"),
            data_table(
                ["Feature", "Practical implementation", "Reliability gate"],
                [
                    ["Cap/hat", "Add an attribute classifier on the person/head crop or a detector class", "Require enough head pixels and confidence."],
                    ["Backpack/bag", "Add an object class and verify overlap with the person box", "Avoid matching a nearby person's bag."],
                    ["Clothing texture", "Small colour histogram + coarse pattern tag", "Use only with usable lighting/trust."],
                    ["Person re-ID vector", "Optional consented re-ID model on full-body crop", "Treat as possible same appearance, not identity."],
                    ["Vehicle shape", "Embedding from the vehicle crop", "Combine with plate/colour; never outweigh a conflicting clear plate."],
                ],
                [32, 87, 51],
            ),
            para("A unique but achievable extension", "h2"),
            para(
                "Create an <b>evidence signature</b>: plate similarity, vehicle embedding, "
                "upper/lower colour, cap/backpack, direction, time gap and Camera Trust. "
                "Store each component and its availability. A rules layer or calibrated "
                "model can then explain which signals caused a repeat-match candidate.",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("13. Troubleshooting", "h1"),
            data_table(
                ["Symptom", "Likely cause", "Fix"],
                [
                    ["Python 3.11 not found", "Launcher/path issue", "Install x64 Python 3.11 and enable Add Python to PATH."],
                    ["Tesseract unavailable", "Not installed or not on PATH", "Set TESSERACT_CMD to tesseract.exe."],
                    ["No events", "Unsupported media, strict threshold, no supported object", "Run doctor; try synthetic reference; lower only one threshold at a time."],
                    ["Plate box but no text", "Small/blurred crop or OCR unavailable", "Inspect plate.jpg; improve capture; install PaddleOCR; keep null if unreadable."],
                    ["False plate on logo/sign", "Contour proposal resembles a plate", "Use plate-specific YOLO; require OCR/vehicle context; add negative tests."],
                    ["Face crop absent", "Face too small/turned/occluded", "Use HEIGHTENED, check YuNet model, test frontal authorized fixture."],
                    ["YOLO warning", "Optional ultralytics package/model missing", "Run setup extra and provide authorized weights; fallback still supports demo."],
                    ["AWS AccessDenied", "Wrong profile/role/prefix", "Run sts get-caller-identity; inspect role and exact S3 resource ARN."],
                    ["AWS publish loops", "Output prefix triggers input rule", "Filter only incoming prefix and video suffix."],
                    ["Slow processing", "Too many sampled/OCR frames", "Increase frame_stride or heavy_frame_interval; shorten clips."],
                ],
                [35, 54, 81],
            ),
            para("Diagnostic commands", "h2"),
            code(
                r"""
python -m sentinel_camera_ai --config config\default.yaml doctor
python -m pytest -q
python -m sentinel_camera_ai --help

# Verify model checksums on Linux/macOS:
cd models
sha256sum -c checksums.sha256
"""
            ),
            para("Safe debugging order", "h2"),
            numbered(
                [
                    "Reproduce with the packaged synthetic fixture.",
                    "Read the event's model_versions and model_notes.",
                    "Inspect best_frame.jpg, annotated_frame.jpg and crops.",
                    "Change one detector/config setting.",
                    "Run tests and compare the same media again.",
                    "Only then check AWS publishing.",
                ]
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("14. How to explain the idea to the team", "h1"),
            callout(
                "One-sentence pitch",
                "My Camera AI turns short camera clips into explainable, quality-scored events "
                "and links possible repeats across cameras without uploading every frame or "
                "pretending uncertain evidence is certain.",
                "good",
            ),
            para("A 90-second explanation", "h2"),
            para(
                "“I built a local-first camera intelligence pipeline. NORMAL mode uses less "
                "compute for basic vehicle/person detection; HEIGHTENED mode adds face presence, "
                "plate OCR and appearance. Each result becomes versioned JSON with evidence, "
                "confidence, model names and a quality score for blur, lighting, obstruction "
                "and resolution. Cross-camera comparison combines plate, vehicle, anonymous "
                "face and appearance evidence, then reports a possible match with reasons. "
                "The demo is local and synthetic; AWS comes next for private storage and, "
                "only if needed, managed Fargate processing.”",
            ),
            para("Five-minute live demo order", "h2"),
            numbered(
                [
                    "Run doctor and show that core dependencies/models are available.",
                    "Run scripts\\run_demo.ps1.",
                    "Show the annotated vehicle frames, plate crops and one event JSON.",
                    "Open comparison.json and show the plate/colour/type reasons.",
                    "Show the synthetic face event, then switch to NORMAL mode.",
                    "Finish with the private S3/Fargate rollout—not an untested live deployment.",
                ]
            ),
            para("Questions teammates may ask", "h2"),
            data_table(
                ["Question", "Strong answer"],
                [
                    ["Is face confidence identity confidence?", "No. Detection confidence only means a face-like region was found. Similarity is separate."],
                    ["Why not upload all video?", "Cost, bandwidth and privacy. Adaptive local processing uploads compact evidence/events."],
                    ["What makes this unique?", "Adaptive compute + quality score + multi-signal, explainable repeat linking with abstention."],
                    ["Will it always work?", "No CV system will. We expose uncertainty and validate on representative authorized data."],
                ],
                [55, 115],
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("15. Verification and handoff checklist", "h1"),
            para("Verified in the packaged build", "h2"),
            bullets(
                [
                    "<b>16 automated tests pass.</b>",
                    "Source compiles successfully.",
                    "Mock SA-style plate AB12CDGP is read in both synthetic clips.",
                    "Vehicle repeat match returns true, score 1.0, HIGH evidence.",
                    "Fictional face detection returns one face around 0.895 confidence.",
                    "Face crop and SFace anonymous embedding are saved.",
                    "Synthetic face comparison returns true, score 1.0, HIGH evidence.",
                    "NORMAL/HEIGHTENED behavior and JSON validation are tested.",
                    "OpenCV model checksums and upstream licence notices are included.",
                ]
            ),
            para("Not falsely claimed", "h2"),
            bullets(
                [
                    "Docker files are included but Docker was unavailable in the build environment.",
                    "The AWS adapter is unit-tested with fake clients; no live account was changed.",
                    "Real South African CCTV accuracy requires a local validation set and plate-specific model.",
                    "Caps/backpacks and calibrated absolute height are documented extensions, not current outputs.",
                ]
            ),
            para("Before the team demo", "h2"),
            data_table(
                ["Check", "Owner", "Done"],
                [
                    ["Run setup and demo on the actual Windows laptop", "Camera AI", "□"],
                    ["Record Python/Tesseract/GPU versions", "Camera AI", "□"],
                    ["Freeze model files and checksums", "Camera AI", "□"],
                    ["Test projector-visible annotated images", "Presenter", "□"],
                    ["Agree JSON fields with backend teammate", "Camera AI + backend", "□"],
                    ["Use only synthetic/authorized media", "Whole team", "□"],
                    ["Prepare local fallback if Wi-Fi/AWS fails", "Whole team", "□"],
                ],
                [103, 47, 20],
            ),
            callout(
                "Definition of done",
                "The demo is ready when a clean Windows machine can run the two scripts, "
                "produce valid events, show evidence, compare repeats and explain every score "
                "without needing live AWS or unconsented data.",
                "good",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            para("Official references", "h1"),
            para(
                "Use these primary sources when updating dependencies or deployment choices. "
                "They may change after this guide's build date.",
            ),
            bullets(
                [
                    "<link href='https://github.com/opencv/opencv_zoo'>OpenCV Zoo — official model repository</link>",
                    "<link href='https://docs.ultralytics.com/'>Ultralytics — official documentation</link>",
                    "<link href='https://paddlepaddle.github.io/PaddleOCR/main/en/quick_start.html'>PaddleOCR — official quick start</link>",
                    "<link href='https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html'>AWS IAM best practices</link>",
                    "<link href='https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html'>AWS CLI IAM Identity Center configuration</link>",
                    "<link href='https://docs.aws.amazon.com/lambda/latest/dg/with-s3.html'>AWS Lambda with S3 events</link>",
                    "<link href='https://docs.aws.amazon.com/lambda/latest/dg/images-create.html'>AWS Lambda container images</link>",
                    "<link href='https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html'>AWS Step Functions integration with ECS/Fargate</link>",
                ]
            ),
            para("Files to read next", "h2"),
            data_table(
                ["File", "Purpose"],
                [
                    ["README.md", "Fast project usage and architecture."],
                    ["BUILD_REPORT.md", "Exact verified and unverified claims."],
                    ["config/default.yaml", "Thresholds, modes, paths and AWS settings."],
                    ["THIRD_PARTY_MODELS.md", "Model provenance and licence cautions."],
                    ["schemas/event-v1.schema.json", "Backend integration contract."],
                    ["demo-output/", "Evidence and comparison examples."],
                ],
                [62, 108],
            ),
            Spacer(1, 9 * mm),
            HRFlowable(width="100%", thickness=1.2, color=CYAN),
            Spacer(1, 5 * mm),
            para(
                "<b>Final recommendation:</b> demonstrate the verified local pipeline first, "
                "collect a small authorized South African validation set second, and enable "
                "private AWS publishing third. That order gives the team a working system, "
                "measurable accuracy and a cloud path without combining every risk at once.",
            ),
        ]
    )
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Sentinel Camera AI — Built System, Setup & AWS Deployment Guide",
        author="Discovery Sentinel Mesh",
        subject="Camera AI implementation, local setup, matching and AWS deployment",
    )
    document.build(build_story(), onFirstPage=page_frame, onLaterPages=page_frame)
    print(OUTPUT)


if __name__ == "__main__":
    main()
