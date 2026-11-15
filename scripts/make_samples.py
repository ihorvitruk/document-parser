"""Generates the sample PDFs in samples/.

    docker run --rm -v "$PWD:/work" -w /work python:3.13-slim sh -c \
      "pip install -q reportlab pillow && python scripts/make_samples.py"

All content is original text written for this project.
"""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path("samples")
styles = getSampleStyleSheet()


def quarterly_report():
    """Text PDF with headings, tables and lists: shows what markdown parsing adds."""
    regions = {
        "Northern Europe": [("Q1", 412, 388), ("Q2", 436, 401), ("Q3", 459, 420), ("Q4", 481, 447)],
        "Southern Europe": [("Q1", 298, 301), ("Q2", 305, 296), ("Q3", 322, 310), ("Q4", 339, 318)],
    }
    story = [
        Paragraph("Acme Logistics: Annual Report 2025", styles["Title"]),
        Paragraph(
            "This report summarizes revenue and costs for the year, by region and quarter. "
            "All figures are in thousands of euros. Revenue grew in every quarter in Northern "
            "Europe, while Southern Europe was flat in the first half and recovered in the second.",
            styles["BodyText"],
        ),
        PageBreak(),
    ]
    for region, rows in regions.items():
        story.append(Paragraph(region, styles["Heading1"]))
        story.append(
            Paragraph(
                f"Quarterly revenue and costs for {region}. Margin is revenue minus costs.",
                styles["BodyText"],
            )
        )
        story.append(Spacer(1, 12))
        data = [["Quarter", "Revenue", "Costs", "Margin"]] + [
            [q, str(r), str(c), str(r - c)] for q, r, c in rows
        ]
        table = Table(data, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 12))
        story.append(
            ListFlowable(
                [
                    ListItem(Paragraph("Fuel prices were the main cost driver.", styles["BodyText"])),
                    ListItem(Paragraph("Two new warehouses opened during the year.", styles["BodyText"])),
                ],
                bulletType="bullet",
            )
        )
        story.append(PageBreak())
    story += [
        Paragraph("Outlook", styles["Heading1"]),
        Paragraph(
            "Management expects moderate growth in 2026. The main risks are fuel price volatility, "
            "labour shortages at the new warehouses, and delays in customs processing.",
            styles["BodyText"],
        ),
    ]
    SimpleDocTemplate(str(OUT / "quarterly-report.pdf"), pagesize=A4, title="Quarterly report").build(story)


def redis_streams_article():
    """Plain prose over a few pages: the simplest case for every parser."""
    paragraphs = [
        ("What is a Redis Stream?", "A Redis Stream is an append-only log. Producers add entries with the XADD command, and every entry gets a unique, increasing identifier. Entries are kept until they are explicitly deleted or trimmed, so a stream can be read again from any point."),
        ("Consumer groups", "A consumer group lets several workers share one stream. Redis delivers each entry to exactly one consumer in the group. The consumer must acknowledge the entry with XACK once it has finished, otherwise Redis keeps it in the pending list."),
        ("Surviving crashes", "If a worker dies before acknowledging an entry, the entry stays pending. Another worker can take it over with XAUTOCLAIM once it has been idle for long enough. Redis also counts how many times an entry was delivered, which makes it possible to give up on a message that keeps failing."),
        ("When to use streams", "Streams are a good fit for work queues that must not lose jobs, for event logs, and for fan-out to several independent consumer groups. For fire-and-forget notifications, plain publish and subscribe is simpler."),
    ]
    story = [Paragraph("Redis Streams in Short", styles["Title"])]
    for index, (heading, text) in enumerate(paragraphs):
        story += [Paragraph(heading, styles["Heading2"]), Paragraph(text, styles["BodyText"]), Spacer(1, 10)]
        if index == 1:
            story.append(PageBreak())
    SimpleDocTemplate(str(OUT / "redis-streams-article.pdf"), pagesize=A4, title="Redis Streams").build(story)


def scanned_memo():
    """Pages that are pictures of text, with no text layer, like a real scan.
    PyPDF finds nothing in it; an LLM parser can read it."""
    pages = [
        [
            "INTERNAL MEMO",
            "",
            "To: Operations team",
            "From: Anna Kowalska, Facilities",
            "Date: 14 March 2025",
            "",
            "Subject: Warehouse B closed on Friday",
            "",
            "Warehouse B will be closed this Friday for the annual",
            "fire safety inspection. All deliveries scheduled for",
            "that day move to Monday. Please tell your customers.",
        ],
        [
            "Action items",
            "",
            "1. Reschedule the 12 Friday deliveries to Monday.",
            "2. Move perishable stock to Warehouse A by Thursday.",
            "3. Send the new delivery dates to customers by Wednesday.",
            "",
            "Questions? Call extension 4410.",
        ],
    ]
    font = ImageFont.load_default(size=30)
    pdf = canvas.Canvas(str(OUT / "scanned-memo.pdf"), pagesize=A4)
    width, height = A4
    for lines in pages:
        image = Image.new("L", (1240, 1754), 245)  # an A4 page at 150 dpi
        draw = ImageDraw.Draw(image)
        y = 140
        for line in lines:
            draw.text((110, y), line, fill=20, font=font)
            y += 56
        buffer = BytesIO()
        image.save(buffer, "PNG")
        buffer.seek(0)
        pdf.drawImage(ImageReader(buffer), 0, 0, width=width, height=height)
        pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    quarterly_report()
    redis_streams_article()
    scanned_memo()
    for path in sorted(OUT.glob("*.pdf")):
        print(f"{path}  {path.stat().st_size // 1024} KB")
