import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Mount Apryse WebViewer library
# webviewer.min.js is at the package root; core/ui assets are in public/
lib_node_modules_path = "../node_modules/@pdftron/webviewer"
lib_local_path = "lib"

if os.path.exists(lib_node_modules_path):
    app.mount("/lib", StaticFiles(directory=lib_node_modules_path), name="lib")
elif os.path.exists(lib_local_path):
    app.mount("/lib", StaticFiles(directory=lib_local_path), name="lib")

# ---------------------------------------------------------
# จำลอง 'Space' (S3) ด้วย Local Folder
# ---------------------------------------------------------
SPACE_DIR = "space"
os.makedirs(SPACE_DIR, exist_ok=True)

PDF_FILE_PATH = os.path.join(SPACE_DIR, "dummy_report.pdf")  # ไฟล์ต้นฉบับ
XFDF_FILE_PATH = os.path.join(SPACE_DIR, "signature_data.xfdf")  # ไฟล์เก็บลายเซ็น


# 1. Endpoint เสิร์ฟหน้า HTML
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


# 2. Endpoint โหลดไฟล์ PDF ต้นฉบับ
@app.get("/api/document")
async def get_pdf_document():
    return FileResponse(PDF_FILE_PATH, media_type="application/pdf")


# 3. Endpoint สำหรับโหลดและเซฟ XFDF (Annotations)
@app.get("/api/xfdf")
async def load_xfdf():
    # ถ้ามีไฟล์ XFDF ใน Space ให้ส่งกลับไปให้หน้าบ้าน
    if os.path.exists(XFDF_FILE_PATH):
        with open(XFDF_FILE_PATH, "r", encoding="utf-8") as f:
            return {"xfdf_payload": f.read()}
    # ถ้ายังไม่มีการเซ็น ให้ inject "กล่องเซ็นชื่อเปล่า" (Signature Widget) ไปที่หน้าบ้านเลย
    # rect="x1,y1,x2,y2" พิกัดในระบบ PDF (origin ซ้ายล่าง) บน A4 (595x842 pt)
    base_xfdf = """<?xml version="1.0" encoding="UTF-8"?>
<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">
  <fields>
    <field name="Executive_Signature"><value></value></field>
  </fields>
  <annots>
    <widget field="Executive_Signature" page="0" rect="350,80,520,140"
            flags="print" name="Executive_Signature"
            title="Executive_Signature" type="Sig" />
  </annots>
</xfdf>"""
    return {"xfdf_payload": base_xfdf}


@app.post("/api/xfdf")
async def save_xfdf(request: Request):
    # รับ XFDF String จากหน้าบ้านแล้วเซฟลง Space
    data = await request.json()
    xfdf_string = data.get("xfdf_payload")

    if xfdf_string:
        with open(XFDF_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(xfdf_string)
        return {"status": "success", "message": "Saved to Space successfully!"}

    return {"status": "error", "message": "No data provided"}
