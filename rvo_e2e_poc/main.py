import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader
from pypdf import PdfWriter, PdfReader
from pypdf.generic import (
    ArrayObject, DictionaryObject, NameObject, NumberObject,
    create_string_object,
)

app = FastAPI()

# --- Mount Static Files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount Apryse WebViewer from node_modules
lib_path = "../node_modules/@pdftron/webviewer"
if os.path.exists(lib_path):
    app.mount("/lib", StaticFiles(directory=lib_path), name="lib")

# --- Setup Paths ---
SPACE_DIR = "space"
os.makedirs(SPACE_DIR, exist_ok=True)
PDF_FILE_PATH = os.path.join(SPACE_DIR, "rvo_generated.pdf")
XFDF_FILE_PATH = os.path.join(SPACE_DIR, "signature.xfdf")

# --- Jinja2 Template Engine ---
env = Environment(loader=FileSystemLoader("templates"))


def embed_signature_field(pdf_path: str):
    """
    Post-process a WeasyPrint PDF to embed an AcroForm Signature Widget.
    Coordinates (PDF units, bottom-left origin): rect = [x1, y1, x2, y2]
    A4 page = 595 x 842 pt — วางกล่องขวาล่าง (ห่างจากล่าง 80-140 pt)
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.clone_reader_document_root(reader)

    # สร้าง Widget Annotation สำหรับ Signature field
    sig_widget = DictionaryObject({
        NameObject("/Type"):    NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"):      NameObject("/Sig"),
        NameObject("/T"):       create_string_object("Executive_Signature"),
        # พิกัด PDF (bottom-left origin) — A4: 595x842pt, margin 2cm≈57pt
        # ชิดซ้าย: x1=57 (left margin), x2=222 (กว้าง 165pt)
        # บนเส้น sig-line (กลางหน้า): y1=480, y2=520
        NameObject("/Rect"):    ArrayObject([
            NumberObject(57),  NumberObject(480),
            NumberObject(222), NumberObject(520),
        ]),
        NameObject("/F"):       NumberObject(4),
        NameObject("/P"):       writer.pages[0].indirect_reference,
    })

    # เพิ่ม widget เข้าไปใน indirect objects และผูกกับหน้าแรก
    widget_ref = writer._add_object(sig_widget)
    page = writer.pages[0]
    if "/Annots" not in page:
        page[NameObject("/Annots")] = ArrayObject()
    page[NameObject("/Annots")].append(widget_ref)

    # สร้าง AcroForm ใน PDF root
    acro_form = DictionaryObject({
        NameObject("/Fields"):   ArrayObject([widget_ref]),
        NameObject("/SigFlags"): NumberObject(3),  # SignaturesExist + AppendOnly
    })
    writer._root_object[NameObject("/AcroForm")] = acro_form

    # เขียนทับไฟล์เดิม
    with open(pdf_path, "wb") as f:
        writer.write(f)


# ---------------------------------------------------------
# 1. Serve Frontend
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------
# 2. API: Generate PDF from HTML template via WeasyPrint
# ---------------------------------------------------------
@app.post("/api/generate-pdf")
async def generate_pdf():
    data = {
        "rvo_number": "RVO-2026-9999",
        "project_name": "AWC Asiatique Expansion",
        "amount": "1,500,000.00",
    }

    template = env.get_template("report.html")
    html_out = template.render(**data)

    # Step 1: WeasyPrint แปลง HTML → PDF
    HTML(string=html_out).write_pdf(PDF_FILE_PATH)

    # Step 2: pypdf embed AcroForm Signature Widget ลงใน PDF โดยตรง
    embed_signature_field(PDF_FILE_PATH)

    # Reset signature when generating a new PDF
    if os.path.exists(XFDF_FILE_PATH):
        os.remove(XFDF_FILE_PATH)

    return {"message": "PDF Generated Successfully!"}


# ---------------------------------------------------------
# 3. API: Serve the generated PDF
# ---------------------------------------------------------
@app.api_route("/api/document", methods=["GET", "HEAD"])
async def get_document():
    if not os.path.exists(PDF_FILE_PATH):
        return JSONResponse({"error": "PDF not generated yet. Click 'Generate PDF' first."}, status_code=404)
    return FileResponse(PDF_FILE_PATH, media_type="application/pdf")


# ---------------------------------------------------------
# 4. API: Load / Save XFDF (annotations + signature)
# ---------------------------------------------------------
@app.get("/api/xfdf")
async def get_xfdf():
    # If a signature was already saved, return it
    if os.path.exists(XFDF_FILE_PATH):
        with open(XFDF_FILE_PATH, "r", encoding="utf-8") as f:
            return {"xfdf_payload": f.read()}

    # Widget is already embedded in PDF via pypdf — no need to inject via XFDF
    # (XFDF <widget> injection causes "Unsupported annotation type: widget" in Apryse)
    return {"xfdf_payload": None}


@app.post("/api/xfdf")
async def save_xfdf(request: Request):
    data = await request.json()
    xfdf_string = data.get("xfdf_payload")
    if xfdf_string:
        with open(XFDF_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(xfdf_string)
        return {"status": "success", "message": "Signature saved to Space!"}
    return JSONResponse({"status": "error", "message": "No data provided"}, status_code=400)


# ---------------------------------------------------------
# 5. Serve Approve Page (v2)
# ---------------------------------------------------------
@app.get("/approve", response_class=HTMLResponse)
async def serve_approve():
    with open("static/approve.html", "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------
# 6. API: Mock Approve
# ---------------------------------------------------------
@app.post("/api/approve")
async def approve():
    import datetime
    ref = f"AWC-APPROVED-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    print(f"[APPROVE] ✅ Document approved — ref: {ref}")
    # TODO: ต่อ logic จริง เช่น update status ใน DB, ส่ง email, etc.
    return {
        "status": "approved",
        "approval_ref": ref,
        "message": "Document has been approved successfully.",
    }
