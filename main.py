import os
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader
from pypdf import PdfWriter, PdfReader

load_dotenv()
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    create_string_object,
)

PDF_ANNOTS = "/Annots"

app = FastAPI()

# --- Mount Static Files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount Apryse WebViewer from node_modules
lib_path = "node_modules/@pdftron/webviewer"
if os.path.exists(lib_path):
    app.mount("/lib", StaticFiles(directory=lib_path), name="lib")

# --- Setup Paths ---
SPACE_DIR = "space"
os.makedirs(SPACE_DIR, exist_ok=True)
PDF_FILE_PATH = os.path.join(SPACE_DIR, "rvo_generated.pdf")
XFDF_FILE_PATH = os.path.join(SPACE_DIR, "signature.xfdf")

# --- Jinja2 Template Engine ---
env = Environment(loader=FileSystemLoader("templates"))


def _make_sig_widget(name: str, rect: list, page_ref) -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Sig"),
            NameObject("/T"): create_string_object(name),
            NameObject("/Rect"): ArrayObject([NumberObject(v) for v in rect]),
            NameObject("/F"): NumberObject(4),
            NameObject("/P"): page_ref,
        }
    )


def embed_signature_field(pdf_path: str):
    """
    Embed two AcroForm Signature Widgets over the QS and G&C approval boxes in PART I C.
    Coordinates measured via WeasyPrint layout (PDF pts, bottom-left origin, A4 595x842pt):
      QS  box: x1=11, y1=302, x2=292, y2=335
      G&C box: x1=298, y1=302, x2=578, y2=335
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.clone_reader_document_root(reader)

    page_ref = writer.pages[0].indirect_reference

    qs_widget = _make_sig_widget("QS_Signature", [11, 302, 292, 335], page_ref)
    gc_widget = _make_sig_widget("GC_Signature", [298, 302, 578, 335], page_ref)

    qs_ref = writer._add_object(qs_widget)
    gc_ref = writer._add_object(gc_widget)

    page = writer.pages[0]
    if PDF_ANNOTS not in page:
        page[NameObject(PDF_ANNOTS)] = ArrayObject()
    page[NameObject(PDF_ANNOTS)].append(qs_ref)
    page[NameObject(PDF_ANNOTS)].append(gc_ref)

    acro_form = DictionaryObject(
        {
            NameObject("/Fields"): ArrayObject([qs_ref, gc_ref]),
            NameObject("/SigFlags"): NumberObject(3),
        }
    )
    writer._root_object[NameObject("/AcroForm")] = acro_form

    with open(pdf_path, "wb") as f:
        writer.write(f)


# ---------------------------------------------------------
# 0. API: Public Client Config (env vars safe to expose)
# ---------------------------------------------------------
@app.get("/api/config")
async def get_config():
    return {
        "PDFTRON_SERVER_URL": os.getenv("PDFTRON_SERVER_URL", ""),
        "PDFTRON_LICENSE": os.getenv("PDFTRON_LICENSE", ""),
    }


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
async def generate_pdf(request: Request):
    try:
        body = await request.json()
        amount = body.get("amount", "1,500,000.00")
    except Exception:
        amount = "1,500,000.00"

    # --- Fetch carried_forward from SAP ---
    url = "https://awc-apricot-dev.assetworldcorp-th.com/PR/PayStation/qas/prlistSet/"
    querystring = {
        "sap-client": "610",
        "$filter": "compcode eq '6053' and doctype eq 'ZRVA' and reqdate ge datetime'2025-10-01T00:00:00'",
        "$expand": "prdetails/praccs,prdetails/prservices",
        "$format": "json",
    }
    headers = {"authorization": "Basic UFJQT0NPTjpWdEAxMjM0NTY3OA=="}

    sum_pr = 0.0
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=10)
        if response.status_code == 200:
            data_json = response.json()
            results = data_json.get("d", {}).get("results", [])
            for res in results:
                prdetails = res.get("prdetails", {}).get("results", [])
                for detail in prdetails:
                    if (
                        detail.get("relind") == "2"
                        and detail.get("contractno") == "4100000931"
                    ):
                        val_price = detail.get("valuationprice")
                        if val_price:
                            sum_pr += float(val_price)
    except Exception as e:
        print("Error fetching SAP data:", e)

    carried_forward_str = f"{sum_pr:,.2f}"

    # Parse amount to calculate
    try:
        clean_amount = amount.replace(",", "")
        amount_val = float(clean_amount)
    except Exception:
        amount_val = 1500000.00

    total_rvo_val = sum_pr + amount_val
    print(
        f"[CALCULATION] Total RVO Formula: Carried Forward ({sum_pr:,.2f}) + Amount ({amount_val:,.2f}) = {total_rvo_val:,.2f}"
    )

    # --- Fetch PO data for estimated_cost ---
    url_po = (
        "https://awc-apricot-dev.assetworldcorp-th.com/PO/PayStation/qas/polistSet/"
    )
    qs_po = {
        "sap-client": "610",
        "$filter": "compcode eq '6053' and (doctype eq '41' or doctype eq '42' or doctype eq 'Z1') and docdate ge datetime'2000-01-15T00:00:00' and pono eq '4100000931'",
        "$expand": "poheaders/poitems/poaccs,poheaders/poitems/poservices",
        "$format": "json",
    }

    sum_po = 0.0
    try:
        res_po = requests.get(url_po, headers=headers, params=qs_po, timeout=10)
        if res_po.status_code == 200:
            po_data_json = res_po.json()

            def get_netpr_sum(node):
                t = 0.0
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == "netpr" and v:
                            try:
                                t += float(v)
                            except ValueError:
                                pass
                        else:
                            t += get_netpr_sum(v)
                elif isinstance(node, list):
                    for i in node:
                        t += get_netpr_sum(i)
                return t

            sum_po = get_netpr_sum(po_data_json)
    except Exception as e:
        print("Error fetching PO data:", e)

    estimated_cost_val = total_rvo_val + sum_po
    print(
        f"[CALCULATION] Estimated Cost Formula: Total RVO ({total_rvo_val:,.2f}) + PO Net Price ({sum_po:,.2f}) = {estimated_cost_val:,.2f}"
    )

    import datetime as dt

    today = dt.date.today().strftime("%d/%m/%Y")

    data = {
        # Header
        "dateIssued": today,
        # PART I A
        "projectName": "AWC Asiatique Expansion",
        "rvoNumber": "RVO-2026-9999",
        "contractorName": "บริษัท ตัวอย่าง จำกัด",
        "contractId": "4100000931",
        "contractDescription": "งานก่อสร้างโครงการ AWC Asiatique Expansion",
        "rvoDescription": f"เพิ่มงาน variation ตามที่ระบุ มูลค่า {amount} บาท",
        "reasons": {
            "designRelated": False,
            "materialSpecs": False,
            "awcRequirement": True,
        },
        "backchargeParties": "",
        "backchargeRvoNumber": "",
        "initiatedByName": "Project Manager",
        "initiatedDate": today,
        # PART I B
        "timeImplication": "0",
        "indicativeSchedule": "-",
        "preparedByName": "QS Engineer",
        "preparedDate": today,
        # PART I C
        "specBillRef": "-",
        "costSchedule": "-",
        "estimateQsAmount": amount,
        "estimateGcAmount": amount,
        "approverQsName": "QS Director",
        "approverQsDate": today,
        "approverGcName": "G&C Director",
        "approverGcDate": today,
        # PART I D
        "contractSum": f"{sum_po:,.2f}",
        "carriedForward": carried_forward_str,
        "totalRvo": f"{total_rvo_val:,.2f}",
        "estimatedCost": f"{estimated_cost_val:,.2f}",
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
        return JSONResponse(
            {"error": "PDF not generated yet. Click 'Generate PDF' first."},
            status_code=404,
        )
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
    return JSONResponse(
        {"status": "error", "message": "No data provided"}, status_code=400
    )


# ---------------------------------------------------------
# 5. Serve Approve Page (v2)
# ---------------------------------------------------------
@app.get("/approve", response_class=HTMLResponse)
async def serve_approve():
    with open("static/approve.html", "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------
# 5b. Serve New Flow Page
# ---------------------------------------------------------
@app.get("/new-flow", response_class=HTMLResponse)
async def serve_new_flow():
    with open("static/new-flow.html", "r", encoding="utf-8") as f:
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
