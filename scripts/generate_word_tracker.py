import os
import zipfile
from datetime import datetime

def generate_docx(output_path: str):
    # XML templates for Word (.docx)
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
    <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
    <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
    <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
    <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

    core_props = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <dc:title>KRI Continuous Audit Engine - Executive Presentation Tracker</dc:title>
    <dc:subject>Continuous Internal Audit KRI Engine - Implementation and Action Items Tracker</dc:subject>
    <dc:creator>Continuous Audit Project Team</dc:creator>
    <cp:keywords>KRI, Continuous Audit, AI, Azure OpenAI, Tracker, Roadmap</cp:keywords>
    <dc:description>Executive status tracker, step-by-step process explanation, completed milestones, and pending action items for UI integration and new KRI onboarding testing.</dc:description>
    <dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}</dcterms:created>
    <dcterms:modified xsi:type="dcterms:W3CDTF">{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}</dcterms:modified>
</cp:coreProperties>'''

    app_props = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
    <Application>Microsoft Word</Application>
    <DocSecurity>0</DocSecurity>
    <ScaleCrop>false</ScaleCrop>
    <Company>Internal Audit &amp; Enterprise Risk</Company>
    <LinksUpToDate>false</LinksUpToDate>
    <SharedDoc>false</SharedDoc>
    <HyperlinksChanged>false</HyperlinksChanged>
    <AppVersion>16.0000</AppVersion>
</Properties>'''

    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:docDefaults>
        <w:rPrDefault>
            <w:rPr>
                <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>
                <w:sz w:val="22"/>
                <w:szCs w:val="22"/>
                <w:color w:val="2D3748"/>
                <w:lang w:val="en-US"/>
            </w:rPr>
        </w:rPrDefault>
        <w:pPrDefault>
            <w:pPr>
                <w:spacing w:after="160" w:line="260" w:lineRule="auto"/>
            </w:pPr>
        </w:pPrDefault>
    </w:docDefaults>
    
    <w:style w:type="paragraph" w:styleId="Heading1">
        <w:name w:val="heading 1"/>
        <w:pPr>
            <w:spacing w:before="360" w:after="180"/>
            <w:outlineLvl w:val="0"/>
        </w:pPr>
        <w:rPr>
            <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
            <w:b/>
            <w:sz w:val="34"/>
            <w:szCs w:val="34"/>
            <w:color w:val="1A365D"/>
        </w:rPr>
    </w:style>
    
    <w:style w:type="paragraph" w:styleId="Heading2">
        <w:name w:val="heading 2"/>
        <w:pPr>
            <w:spacing w:before="260" w:after="120"/>
            <w:outlineLvl w:val="1"/>
        </w:pPr>
        <w:rPr>
            <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
            <w:b/>
            <w:sz w:val="28"/>
            <w:szCs w:val="28"/>
            <w:color w:val="2B6CB0"/>
        </w:rPr>
    </w:style>

    <w:style w:type="paragraph" w:styleId="Heading3">
        <w:name w:val="heading 3"/>
        <w:pPr>
            <w:spacing w:before="180" w:after="80"/>
            <w:outlineLvl w:val="2"/>
        </w:pPr>
        <w:rPr>
            <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
            <w:b/>
            <w:sz w:val="24"/>
            <w:szCs w:val="24"/>
            <w:color w:val="2C5282"/>
        </w:rPr>
    </w:style>
    
    <w:style w:type="paragraph" w:styleId="Title">
        <w:name w:val="Title"/>
        <w:pPr>
            <w:spacing w:before="120" w:after="240"/>
            <w:jc w:val="center"/>
        </w:pPr>
        <w:rPr>
            <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
            <w:b/>
            <w:sz w:val="44"/>
            <w:szCs w:val="44"/>
            <w:color w:val="0F2942"/>
        </w:rPr>
    </w:style>

    <w:style w:type="paragraph" w:styleId="Subtitle">
        <w:name w:val="Subtitle"/>
        <w:pPr>
            <w:spacing w:before="0" w:after="360"/>
            <w:jc w:val="center"/>
        </w:pPr>
        <w:rPr>
            <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
            <w:i/>
            <w:sz w:val="24"/>
            <w:szCs w:val="24"/>
            <w:color w:val="4A5568"/>
        </w:rPr>
    </w:style>
</w:styles>'''

    # Helpers for building document.xml
    def p(text="", bold=False, italic=False, color="2D3748", size=22, align="left", space_before=0, space_after=140, style=None):
        style_tag = f'<w:pStyle w:val="{style}"/>' if style else ''
        jc_tag = f'<w:jc w:val="{align}"/>' if align != "left" else ''
        spacing_tag = f'<w:spacing w:before="{space_before}" w:after="{space_after}" w:line="260" w:lineRule="auto"/>'
        
        b_tag = '<w:b/>' if bold else ''
        i_tag = '<w:i/>' if italic else ''
        color_tag = f'<w:color w:val="{color}"/>'
        sz_tag = f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        
        text_xml = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        return f'''<w:p>
            <w:pPr>
                {style_tag}
                {jc_tag}
                {spacing_tag}
            </w:pPr>
            <w:r>
                <w:rPr>
                    <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
                    {b_tag}
                    {i_tag}
                    {color_tag}
                    {sz_tag}
                </w:rPr>
                <w:t xml:space="preserve">{text_xml}</w:t>
            </w:r>
        </w:p>'''

    def callout(title, text, bg_color="EBF8FF", border_color="3182CE"):
        title_xml = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text_xml = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'''<w:tbl>
            <w:tblPr>
                <w:tblW w:w="9600" w:type="dxa"/>
                <w:tblBorders>
                    <w:top w:val="none"/>
                    <w:left w:val="single" w:sz="24" w:space="0" w:color="{border_color}"/>
                    <w:bottom w:val="none"/>
                    <w:right w:val="none"/>
                    <w:insideH w:val="none"/>
                    <w:insideV w:val="none"/>
                </w:tblBorders>
                <w:tblCellMar>
                    <w:top w:w="160" w:type="dxa"/>
                    <w:left w:w="240" w:type="dxa"/>
                    <w:bottom w:w="160" w:type="dxa"/>
                    <w:right w:w="240" w:type="dxa"/>
                </w:tblCellMar>
            </w:tblPr>
            <w:tr>
                <w:tc>
                    <w:tcPr>
                        <w:tcW w:w="9600" w:type="dxa"/>
                        <w:shd w:val="clear" w:color="auto" w:fill="{bg_color}"/>
                    </w:tcPr>
                    <w:p>
                        <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>
                        <w:r>
                            <w:rPr><w:b/><w:sz w:val="22"/><w:color w:val="{border_color}"/></w:rPr>
                            <w:t>{title_xml}</w:t>
                        </w:r>
                    </w:p>
                    <w:p>
                        <w:pPr><w:spacing w:before="0" w:after="60"/></w:pPr>
                        <w:r>
                            <w:rPr><w:sz w:val="20"/><w:color w:val="2D3748"/></w:rPr>
                            <w:t>{text_xml}</w:t>
                        </w:r>
                    </w:p>
                </w:tc>
            </w:tr>
        </w:tbl>
        <w:p><w:pPr><w:spacing w:before="0" w:after="100"/></w:pPr></w:p>'''

    def table(headers, rows, col_widths, alignments=None):
        if not alignments:
            alignments = ["left"] * len(headers)
            
        xml = ['''<w:tbl>
            <w:tblPr>
                <w:tblW w:w="9600" w:type="dxa"/>
                <w:tblBorders>
                    <w:top w:val="single" w:sz="8" w:space="0" w:color="CBD5E0"/>
                    <w:left w:val="single" w:sz="8" w:space="0" w:color="CBD5E0"/>
                    <w:bottom w:val="single" w:sz="8" w:space="0" w:color="CBD5E0"/>
                    <w:right w:val="single" w:sz="8" w:space="0" w:color="CBD5E0"/>
                    <w:insideH w:val="single" w:sz="6" w:space="0" w:color="E2E8F0"/>
                    <w:insideV w:val="single" w:sz="6" w:space="0" w:color="E2E8F0"/>
                </w:tblBorders>
                <w:tblCellMar>
                    <w:top w:w="120" w:type="dxa"/>
                    <w:left w:w="160" w:type="dxa"/>
                    <w:bottom w:w="120" w:type="dxa"/>
                    <w:right w:w="160" w:type="dxa"/>
                </w:tblCellMar>
            </w:tblPr>''']
            
        # Header Row
        xml.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>')
        for idx, (h, w) in enumerate(zip(headers, col_widths)):
            align = alignments[idx] if idx < len(alignments) else "left"
            h_clean = h.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml.append(f'''<w:tc>
                <w:tcPr>
                    <w:tcW w:w="{w}" w:type="dxa"/>
                    <w:shd w:val="clear" w:color="auto" w:fill="1A365D"/>
                </w:tcPr>
                <w:p>
                    <w:pPr>
                        <w:jc w:val="{align}"/>
                        <w:spacing w:before="60" w:after="60"/>
                    </w:pPr>
                    <w:r>
                        <w:rPr>
                            <w:b/>
                            <w:sz w:val="20"/>
                            <w:color w:val="FFFFFF"/>
                        </w:rPr>
                        <w:t>{h_clean}</w:t>
                    </w:r>
                </w:p>
            </w:tc>''')
        xml.append('</w:tr>')
        
        # Data Rows
        for r_idx, row in enumerate(rows):
            bg = "F7FAFC" if r_idx % 2 == 1 else "FFFFFF"
            xml.append('<w:tr>')
            for c_idx, (cell, w) in enumerate(zip(row, col_widths)):
                align = alignments[c_idx] if c_idx < len(alignments) else "left"
                cell_clean = cell.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                
                # Check for status tags to apply color formatting
                cell_color = "2D3748"
                cell_bold = False
                tc_bg = bg
                if "COMPLETED" in cell:
                    tc_bg = "C6F6D5"
                    cell_color = "22543D"
                    cell_bold = True
                elif "PENDING" in cell:
                    tc_bg = "FEEBC8"
                    cell_color = "7B341E"
                    cell_bold = True
                elif "IN PROGRESS" in cell:
                    tc_bg = "EBF8FF"
                    cell_color = "2B6CB0"
                    cell_bold = True
                elif "HIGH" in cell or "CRITICAL" in cell:
                    cell_color = "9B2C2C"
                    cell_bold = True
                    
                xml.append(f'''<w:tc>
                    <w:tcPr>
                        <w:tcW w:w="{w}" w:type="dxa"/>
                        <w:shd w:val="clear" w:color="auto" w:fill="{tc_bg}"/>
                    </w:tcPr>
                    <w:p>
                        <w:pPr>
                            <w:jc w:val="{align}"/>
                            <w:spacing w:before="40" w:after="40"/>
                        </w:pPr>
                        <w:r>
                            <w:rPr>
                                {'<w:b/>' if cell_bold else ''}
                                <w:sz w:val="18"/>
                                <w:color w:val="{cell_color}"/>
                            </w:rPr>
                            <w:t>{cell_clean}</w:t>
                        </w:r>
                    </w:p>
                </w:tc>''')
            xml.append('</w:tr>')
            
        xml.append('</w:tbl>')
        xml.append('<w:p><w:pPr><w:spacing w:before="0" w:after="140"/></w:pPr></w:p>')
        return "".join(xml)

    # Build document content
    doc_body = []
    
    # Title Block
    doc_body.append(p("CONTINUOUS INTERNAL AUDIT KRI ENGINE", bold=True, size=40, color="0F2942", align="center", space_before=100, space_after=100))
    doc_body.append(p("Program Implementation Tracker & Executive Presentation Roadmap", italic=True, size=24, color="4A5568", align="center", space_before=0, space_after=200))
    
    # Executive Metadata Box
    doc_body.append(callout(
        "Executive Summary & Deliverable Context",
        "This presentation tracker provides a comprehensive step-by-step walkthrough of the Continuous Internal Audit Key Risk Indicator (KRI) Engine architecture. It categorizes completed technical milestones (Backend Engine, Deterministic Tools, Azure OpenAI Orchestration, Multi-Target Logging, and 100% Test Suite) alongside pending action items (Frontend UI Integration and New KRI Onboarding Functionality Testing) into structured, presentation-ready workstreams.",
        bg_color="F0F4F8",
        border_color="1A365D"
    ))
    
    # SECTION 1: EXECUTIVE DASHBOARD
    doc_body.append(p("1. Executive Scorecard & Program Health", bold=True, size=28, color="1A365D", space_before=240, space_after=120, style="Heading1"))
    
    scorecard_headers = ["Program Dimension", "Status", "Completion %", "Key Deliverables / Milestone Summary"]
    scorecard_widths = [2400, 1600, 1400, 4200]
    scorecard_rows = [
        ["Phase 1: Backend Engine & Infrastructure", "COMPLETED", "100%", "FastAPI, SQLite, 7 Repositories, DB Models, Security & Config"],
        ["Phase 1: Deterministic Audit Tools (7 Tools)", "COMPLETED", "100%", "Data Fetch, Compare, Variance, Thresholds, Metrics, Evidence, Plain English"],
        ["Phase 1: Agentic Orchestration Layer", "COMPLETED", "100%", "Azure OpenAI Function Calling, Loop Protection, Scoped Tokens, Mock LLM"],
        ["Phase 1: Logging & Traceability", "COMPLETED", "100%", "Multi-Target Logs (File, JSON Events, Database Records, Console)"],
        ["Phase 1: Automated Test Suite", "COMPLETED", "100%", "16/16 Tests Passing (Unit & Integration, 100% Green)"],
        ["Phase 2: UI Integration (Person B)", "PENDING", "0%", "Frontend Web App, KRI Config UI, Audit Dashboard, Evidence Viewer"],
        ["Phase 3: Onboarding New KRIs Functionality Testing", "PENDING", "35%", "Backend supports dynamic creation; UI flow & multi-area UAT pending"]
    ]
    doc_body.append(table(scorecard_headers, scorecard_rows, scorecard_widths, ["left", "center", "center", "left"]))
    
    # SECTION 2: STEP-BY-STEP PROCESS WORKFLOW
    doc_body.append(p("2. End-to-End Step-by-Step KRI Audit Process", bold=True, size=28, color="1A365D", space_before=280, space_after=120, style="Heading1"))
    doc_body.append(p("The KRI Engine executes internal audit reconciliations through a deterministic, 11-step agentic workflow that guarantees zero LLM hallucination and complete mathematical reproducibility:", size=20, color="4A5568", space_after=120))
    
    steps_headers = ["Step #", "Process Stage", "Component / Mechanism", "Business & Technical Objective", "Status"]
    steps_widths = [800, 2000, 2400, 3200, 1200]
    steps_rows = [
        ["Step 1", "KRI Definition & Setup", "KRI Master Configuration", "Define Process Area, Indicator Type (Leading/Lagging), Risk Description, and End Goal.", "COMPLETED"],
        ["Step 2", "Audit Test Step Formulation", "Test Step Builder", "Formulate natural language audit procedures mapped to sequential test steps with reorder support.", "COMPLETED"],
        ["Step 3", "Threshold & Schedule Binding", "KRI Threshold & Schedule ORM", "Attach numerical tolerance rules (e.g. 10% mismatch) and automated execution frequency.", "COMPLETED"],
        ["Step 4", "Execution Plan Generation", "Plan Service Validator", "Generate deterministic JSON execution plan, validate syntax and safety, transition to ACTIVE.", "COMPLETED"],
        ["Step 5", "Audit Run Initialization", "Audit Execution Coordinator", "Initialize audit run session, create audit_runs database record, and allocate run reference.", "COMPLETED"],
        ["Step 6", "Agentic Orchestration Loop", "Azure OpenAI Function Calling", "LLM interprets planned test steps and requests registered tool invocations (GPT-4o / Mock).", "COMPLETED"],
        ["Step 7", "Scoped Data Extraction", "Tool 1: fetch_financial_data", "Deterministic query of financial datasets (Order Intake & Red Box POs) using tokenized handles.", "COMPLETED"],
        ["Step 8", "Deterministic Record Matching", "Tool 2: compare_records", "Correlate transactions across systems (Order ID & PO Reference) identifying matches and gaps.", "COMPLETED"],
        ["Step 9", "Variance & Threshold Math", "Tools 3 & 4: calculate_difference & apply_threshold", "Calculate absolute/percent variance; deterministically assign severity (LOW, MED, HIGH, CRITICAL).", "COMPLETED"],
        ["Step 10", "Metrics & Evidence Generation", "Tools 5, 6 & 7: calculate_kri_metrics & build_evidence", "Calculate population rates, generate SHA-256 evidence package, and plain English explanation.", "COMPLETED"],
        ["Step 11", "Logging & Exception Review", "Multi-Target Logger & Review Workflow", "Persist logs to File/JSON/DB; expose REST endpoints for Human-in-the-Loop review & sign-off.", "COMPLETED"]
    ]
    doc_body.append(table(steps_headers, steps_rows, steps_widths, ["center", "left", "left", "left", "center"]))
    
    # SECTION 3: DETAILED ACTION ITEMS TRACKER
    doc_body.append(p("3. Detailed Action Items Tracker (Completed vs. Pending)", bold=True, size=28, color="1A365D", space_before=280, space_after=120, style="Heading1"))
    
    # Workstream 1
    doc_body.append(p("Workstream 1: Foundation, Infrastructure & Repositories (Phase 1)", bold=True, size=22, color="2B6CB0", space_before=160, space_after=80, style="Heading2"))
    ws1_headers = ["Action Item ID", "Deliverable / Task Description", "Type", "Status", "Owner", "Outcome / Evidence"]
    ws1_widths = [1400, 3600, 1200, 1200, 1000, 1200]
    ws1_rows = [
        ["ACT-FND-01", "SQLite Schema & SQLAlchemy 2.0 ORM Models (KRI, Financial, Audit, Logs)", "Core", "COMPLETED", "Backend", "Verified"],
        ["ACT-FND-02", "Pydantic v2 Schemas for Strict Data Validation & Serialization", "Core", "COMPLETED", "Backend", "Verified"],
        ["ACT-FND-03", "Data Repositories (KRIRepository, FinancialRepository, AuditRepository)", "Repository", "COMPLETED", "Backend", "Verified"],
        ["ACT-FND-04", "Configuration & Environment Management (.env, Pydantic Settings)", "Infra", "COMPLETED", "Backend", "Verified"],
        ["ACT-FND-05", "Execution Plan Generator & Safety Validator (app/services/plan_service.py)", "Service", "COMPLETED", "Backend", "Verified"]
    ]
    doc_body.append(table(ws1_headers, ws1_rows, ws1_widths, ["left", "left", "center", "center", "center", "center"]))

    # Workstream 2
    doc_body.append(p("Workstream 2: Deterministic Tools & Agentic Orchestrator (Phase 1)", bold=True, size=22, color="2B6CB0", space_before=160, space_after=80, style="Heading2"))
    ws2_headers = ["Action Item ID", "Deliverable / Task Description", "Type", "Status", "Owner", "Outcome / Evidence"]
    ws2_widths = [1400, 3600, 1200, 1200, 1000, 1200]
    ws2_rows = [
        ["ACT-TLS-01", "Tool 1: fetch_financial_data (Scoped dataset retrieval & tokenized handles)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-TLS-02", "Tool 2: compare_records (Deterministic matching & gap identification)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-TLS-03", "Tool 3: calculate_difference (Absolute/percentage variance calculation)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-TLS-04", "Tool 4: apply_threshold (Multi-tier threshold evaluation & severity logic)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-TLS-05", "Tool 5: calculate_kri_metrics (Aggregate metrics & exposure % calculation)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-TLS-06", "Tool 6: build_evidence (SHA-256 evidence package generation & provenance)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-TLS-07", "Tool 7: generate_explanation (Plain English explanation with numeric integrity)", "Tool", "COMPLETED", "Backend", "Verified"],
        ["ACT-AGT-01", "Agent Orchestrator (Function calling loop, recursion limits, error recovery)", "Agent", "COMPLETED", "Backend", "Verified"],
        ["ACT-AGT-02", "Dual LLM Provider Implementation (Azure OpenAI + Deterministic Mock LLM)", "LLM", "COMPLETED", "Backend", "Verified"]
    ]
    doc_body.append(table(ws2_headers, ws2_rows, ws2_widths, ["left", "left", "center", "center", "center", "center"]))

    # Workstream 3
    doc_body.append(p("Workstream 3: REST APIs, Logging & Testing Suite (Phase 1)", bold=True, size=22, color="2B6CB0", space_before=160, space_after=80, style="Heading2"))
    ws3_headers = ["Action Item ID", "Deliverable / Task Description", "Type", "Status", "Owner", "Outcome / Evidence"]
    ws3_widths = [1400, 3600, 1200, 1200, 1000, 1200]
    ws3_rows = [
        ["ACT-API-01", "KRI REST Endpoints (CRUD, step reorder, validation, plan generation)", "API", "COMPLETED", "Backend", "Swagger Ready"],
        ["ACT-API-02", "Audit REST Endpoints (Run execution, exceptions, trace, evidence package)", "API", "COMPLETED", "Backend", "Swagger Ready"],
        ["ACT-LOG-01", "4-Tier Logging System (File Log, Structured JSON, SQLite DB, Console)", "Logging", "COMPLETED", "Backend", "Verified"],
        ["ACT-DAT-01", "80 Deterministic Financial Mock Records (Exact match, variance, missing PO)", "Mock Data", "COMPLETED", "Backend", "Verified"],
        ["ACT-TST-01", "Automated Pytest Suite (16/16 Unit & Integration Tests, 100% Green)", "QA", "COMPLETED", "Backend", "Passing"]
    ]
    doc_body.append(table(ws3_headers, ws3_rows, ws3_widths, ["left", "left", "center", "center", "center", "center"]))

    # Workstream 4 (PENDING: UI INTEGRATION)
    doc_body.append(p("Workstream 4: UI Integration (Person B Frontend) - PENDING ACTION ITEMS", bold=True, size=22, color="C53030", space_before=200, space_after=80, style="Heading2"))
    doc_body.append(callout(
        "Critical Dependency: Person B Frontend Collaboration",
        "The backend is fully operational with interactive OpenAPI documentation at /docs and CORS enabled. The following action items constitute the pending UI integration deliverables for Person B.",
        bg_color="FFF5F5",
        border_color="E53E3E"
    ))
    ws4_headers = ["Action Item ID", "Action Item / UI Feature Deliverable", "Priority", "Status", "Assigned", "Target Sprint"]
    ws4_widths = [1400, 3600, 1200, 1200, 1000, 1200]
    ws4_rows = [
        ["ACT-UI-01", "API Contract Walkthrough & Swagger Client Generation with Person B", "HIGH", "PENDING", "Person B", "Sprint 1"],
        ["ACT-UI-02", "KRI Master Catalog & Configuration Screen (Create, Edit, Process Area filter)", "HIGH", "PENDING", "Person B", "Sprint 1"],
        ["ACT-UI-03", "Interactive Natural Language Test Step Builder & Drag-and-Drop Reorder UI", "HIGH", "PENDING", "Person B", "Sprint 1"],
        ["ACT-UI-04", "Execution Plan Preview & One-Click Plan Validation Modal", "MEDIUM", "PENDING", "Person B", "Sprint 2"],
        ["ACT-UI-05", "Audit Run Execution Dashboard with Date Pickers & Real-Time Progress", "HIGH", "PENDING", "Person B", "Sprint 2"],
        ["ACT-UI-06", "KRI Metrics Summary Cards (Total Transactions, Exception %, Mismatch $)", "HIGH", "PENDING", "Person B", "Sprint 2"],
        ["ACT-UI-07", "Exception Management Grid with Filter by Severity, Type & Reason Code", "HIGH", "PENDING", "Person B", "Sprint 2"],
        ["ACT-UI-08", "Evidence Drawer & Plain-English Explanation Modal with SHA-256 verification", "HIGH", "PENDING", "Person B", "Sprint 3"],
        ["ACT-UI-09", "Step-by-Step Agentic Tool Trace & Real-Time Execution Log Viewer", "MEDIUM", "PENDING", "Person B", "Sprint 3"],
        ["ACT-UI-10", "Human-in-the-Loop Exception Review Action (Approve / Reject / Remediate)", "HIGH", "PENDING", "Person B", "Sprint 3"]
    ]
    doc_body.append(table(ws4_headers, ws4_rows, ws4_widths, ["left", "left", "center", "center", "center", "center"]))

    # Workstream 5 (PENDING: NEW KRI ONBOARDING TESTING)
    doc_body.append(p("Workstream 5: Onboarding New KRI Functionality Testing - PENDING ACTION ITEMS", bold=True, size=22, color="DD6B20", space_before=200, space_after=80, style="Heading2"))
    doc_body.append(callout(
        "Scope: Dynamic Onboarding & Cross-Domain Validation",
        "Validating that audit administrators can dynamically onboard novel KRIs across different business cycles (e.g. Procure-to-Pay, Payroll, Inventory) without requiring codebase modifications.",
        bg_color="FFFAF0",
        border_color="DD6B20"
    ))
    ws5_headers = ["Action Item ID", "Testing Action Item / Scenario", "Priority", "Status", "Assigned", "Target Sprint"]
    ws5_widths = [1400, 3600, 1200, 1200, 1000, 1200]
    ws5_rows = [
        ["ACT-ONB-01", "Onboard KRI-O2C-002: Order Booking vs Signed Contract Price Variance", "HIGH", "PENDING", "QA / Audit", "Sprint 2"],
        ["ACT-ONB-02", "Onboard KRI-P2P-001: 3-Way Matching (Vendor Invoice vs Goods Receipt vs PO)", "HIGH", "PENDING", "QA / Audit", "Sprint 2"],
        ["ACT-ONB-03", "Onboard KRI-TR-001: Duplicate Payment & Mismatched Bank Details Detection", "MEDIUM", "PENDING", "QA / Audit", "Sprint 3"],
        ["ACT-ONB-04", "Test Dynamic Test Step Insertion, Step Deletion & Reordering Resilience", "HIGH", "PENDING", "Backend / QA", "Sprint 2"],
        ["ACT-ONB-05", "Test Custom Tolerance Thresholds (Absolute Dollar Thresholds vs % Variance)", "HIGH", "PENDING", "Backend / QA", "Sprint 2"],
        ["ACT-ONB-06", "Validate Multi-Source System Data Ingestion (Sapiens, SAP ECC, Red Box, SharePoint)", "MEDIUM", "PENDING", "Backend / QA", "Sprint 3"],
        ["ACT-ONB-07", "Simulate Ambiguous & Corrupt Data Inputs (Missing keys, null values, currency mismatch)", "HIGH", "PENDING", "QA / Security", "Sprint 3"],
        ["ACT-ONB-08", "End-to-End User Acceptance Testing (UAT) with Internal Audit Stakeholders", "HIGH", "PENDING", "Audit Leads", "Sprint 3"]
    ]
    doc_body.append(table(ws5_headers, ws5_rows, ws5_widths, ["left", "left", "center", "center", "center", "center"]))

    # SECTION 4: ROADMAP & TIMELINE
    doc_body.append(p("4. Sprint Execution Timeline & Milestone Roadmap", bold=True, size=28, color="1A365D", space_before=280, space_after=120, style="Heading1"))
    
    timeline_headers = ["Sprint / Phase", "Focus Area", "Key Deliverables", "Dependencies", "Target Status"]
    timeline_widths = [1600, 2200, 3400, 1400, 1000]
    timeline_rows = [
        ["Sprint 0 (Completed)", "Backend Engine Core", "Deterministic Tools, Agent Orchestrator, SQLite, 16 Unit/Integration Tests", "None", "COMPLETED"],
        ["Sprint 1 (Weeks 1-2)", "UI Foundations & Setup", "Person B Handshake, KRI Catalog UI, Test Step Builder, API Wireframes", "Backend APIs", "IN PROGRESS"],
        ["Sprint 2 (Weeks 3-4)", "Audit Dashboard & Onboarding", "Audit Run Screen, Metrics Display, Exception Grid, KRI-O2C-002 Onboarding", "Sprint 1 UI", "PLANNED"],
        ["Sprint 3 (Weeks 5-6)", "Evidence UI & UAT Testing", "Evidence Drawer, Trace Log Viewer, Review Sign-off, P2P KRI Testing, UAT", "Sprint 2 UI", "PLANNED"],
        ["Sprint 4 (Weeks 7-8)", "Hardening & Executive Demo", "End-to-End Governance Audit, Performance Optimization, Final Presentation", "UAT Sign-off", "PLANNED"]
    ]
    doc_body.append(table(timeline_headers, timeline_rows, timeline_widths, ["left", "left", "left", "center", "center"]))

    # SECTION 5: GOVERNANCE & ARCHITECTURAL ADVANTAGES
    doc_body.append(p("5. Governance Safeguards & Presentation Highlights", bold=True, size=28, color="1A365D", space_before=280, space_after=120, style="Heading1"))
    doc_body.append(callout(
        "Key Governance Guarantee: Zero Hallucination Financial Math",
        "The architecture decouples natural language reasoning from numerical execution. The LLM acts solely as a function caller and never calculates percentages, aggregates amounts, or writes unvetted SQL queries. All calculations are strictly executed by deterministic Python/SQLAlchemy tools.",
        bg_color="F0FFF4",
        border_color="38A169"
    ))
    
    doc_body.append(p("Summary of Key Technical Guarantees for Stakeholders:", bold=True, size=20, color="1A365D", space_before=100, space_after=80))
    doc_body.append(p("• Cryptographic Reproducibility: Every exception is stamped with a SHA-256 evidence record containing exact input payloads, timestamp, and parameter states.", size=20, space_after=60))
    doc_body.append(p("• Scoped Data Handling: Financial datasets remain within the secure database context; the LLM is passed tokenized reference handles rather than raw transaction tables.", size=20, space_after=60))
    doc_body.append(p("• Infinite Loop Prevention: The agent orchestrator enforces a hard max invocation threshold (15 tool calls) and catches malformed tool arguments gracefully.", size=20, space_after=60))
    doc_body.append(p("• Multi-Target Audit Trail: Complete parity between disk log files, structured JSON event streams, relational database logs, and live console streams.", size=20, space_after=140))
    
    doc_body_xml = "".join(doc_body)
    
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <w:body>
        {doc_body_xml}
        <w:sectPr>
            <w:pgSz w:w="12240" w:h="15840"/>
            <w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/>
            <w:cols w:space="720"/>
            <w:docGrid w:linePitch="360"/>
        </w:sectPr>
    </w:body>
</w:document>'''

    # Create the zip (.docx) file
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', rels)
        zf.writestr('docProps/core.xml', core_props)
        zf.writestr('docProps/app.xml', app_props)
        zf.writestr('word/_rels/document.xml.rels', doc_rels)
        zf.writestr('word/styles.xml', styles)
        zf.writestr('word/document.xml', document_xml)

if __name__ == "__main__":
    out_file = os.path.join(os.path.dirname(__file__), "KRI_Engine_Executive_Presentation_Tracker.docx")
    generate_docx(out_file)
    print(f"Successfully generated Word document at: {out_file}")
