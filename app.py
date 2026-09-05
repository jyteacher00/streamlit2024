import csv
import json
import re
import uuid
import zipfile
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree as ET

import streamlit as st

DATA_DIR = Path("data")
STUDENTS_FILE = DATA_DIR / "students.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
RESPONSES_FILE = DATA_DIR / "responses.csv"
COUNSELING_FILE = DATA_DIR / "counseling.csv"

POSITIVE_EMOTIONS = [
    ("😊", "기뻐요"),
    ("😄", "신나요"),
    ("🥰", "사랑받아요"),
    ("😌", "평온해요"),
    ("🤩", "기대돼요"),
    ("😎", "자신있어요"),
    ("🙏", "고마워요"),
    ("🙂", "괜찮아요"),
    ("💪", "힘나요"),
    ("🌈", "희망적이에요"),
]
NEGATIVE_EMOTIONS = [
    ("😢", "슬퍼요"),
    ("😡", "화나요"),
    ("😰", "불안해요"),
    ("😴", "피곤해요"),
    ("😞", "속상해요"),
    ("😕", "혼란스러워요"),
    ("😔", "외로워요"),
    ("😨", "무서워요"),
    ("🤒", "아파요"),
    ("😤", "답답해요"),
]
DEFAULT_QUESTION = "오늘 아침 나의 마음을 한 문장으로 적어볼까요?"


def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    if not STUDENTS_FILE.exists():
        save_json(STUDENTS_FILE, [])
    if not SETTINGS_FILE.exists():
        save_json(
            SETTINGS_FILE,
            {
                "question": DEFAULT_QUESTION,
                "visible_emotions": [label for _, label in POSITIVE_EMOTIONS + NEGATIVE_EMOTIONS],
            },
        )
    if not RESPONSES_FILE.exists():
        write_csv(RESPONSES_FILE, [], ["date", "student_id", "name", "emotion", "answer", "submitted_at"])
    if not COUNSELING_FILE.exists():
        write_csv(COUNSELING_FILE, [], ["date", "student_id", "name", "counseling_note", "updated_at"])


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path, row, fieldnames):
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def normalize_header(value):
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def read_student_rows(uploaded_file):
    name = uploaded_file.name.lower()
    raw = uploaded_file.getvalue()
    if name.endswith(".csv"):
        text = raw.decode("utf-8-sig")
        return list(csv.DictReader(StringIO(text)))
    if not name.endswith(".xlsx"):
        raise ValueError("CSV 또는 XLSX 파일만 업로드할 수 있습니다.")
    return read_xlsx_first_sheet(raw)


def read_xlsx_first_sheet(raw):
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root.findall("a:si", ns):
                shared.append("".join(t.text or "" for t in item.findall(".//a:t", ns)))
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        first_sheet = workbook.find("a:sheets/a:sheet", ns).attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        target = next(rel.attrib["Target"] for rel in rels if rel.attrib["Id"] == first_sheet)
        sheet_xml = zf.read(f"xl/{target.lstrip('/')}")
        sheet = ET.fromstring(sheet_xml)
        rows = []
        for row in sheet.findall(".//a:row", ns):
            values = []
            for cell in row.findall("a:c", ns):
                value = cell.find("a:v", ns)
                text = value.text if value is not None else ""
                if cell.attrib.get("t") == "s" and text:
                    text = shared[int(text)]
                values.append(text)
            rows.append(values)
    if not rows:
        return []
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:] if any(str(v).strip() for v in row)]


def import_students(uploaded_file):
    rows = read_student_rows(uploaded_file)
    students = []
    for row in rows:
        mapped = {normalize_header(k): v for k, v in row.items()}
        student_id = mapped.get("학번") or mapped.get("번호") or mapped.get("student_id") or mapped.get("id")
        name = mapped.get("이름") or mapped.get("성명") or mapped.get("name")
        if student_id and name:
            students.append({"student_id": str(student_id).strip(), "name": str(name).strip()})
    if not students:
        raise ValueError("명단에는 '학번'과 '이름' 컬럼이 필요합니다.")
    save_json(STUDENTS_FILE, students)
    return students


def get_today_response(student_id):
    today = date.today().isoformat()
    for row in read_csv(RESPONSES_FILE):
        if row.get("date") == today and row.get("student_id") == student_id:
            return row
    return None


def render_student(settings, students):
    st.header("🌞 아침 마음 출석")
    st.caption("처음 한 번만 학번과 이름을 등록하면 같은 스마트폰에서 자동으로 확인됩니다.")
    saved_key = st.session_state.get("student_key") or st.query_params.get("student_key")
    student = next((s for s in students if s.get("student_key") == saved_key), None)

    if student is None:
        with st.form("student_register"):
            student_id = st.text_input("학번")
            name = st.text_input("이름")
            submitted = st.form_submit_button("등록하고 시작하기", type="primary")
        if submitted:
            matched = next((s for s in students if s["student_id"] == student_id.strip() and s["name"] == name.strip()), None)
            if matched is None:
                st.error("교사용 명단에 등록된 학번과 이름을 확인해 주세요.")
                return
            matched["student_key"] = matched.get("student_key") or str(uuid.uuid4())
            save_json(STUDENTS_FILE, students)
            st.session_state["student_key"] = matched["student_key"]
            st.query_params["student_key"] = matched["student_key"]
            st.rerun()
        return

    st.success(f"{student['name']} 학생, 좋은 아침이에요!")
    if get_today_response(student["student_id"]):
        st.info("오늘 답변은 이미 제출되었습니다.")
        return

    visible = set(settings.get("visible_emotions", []))
    options = [f"{emoji} {label}" for emoji, label in POSITIVE_EMOTIONS + NEGATIVE_EMOTIONS if label in visible]
    with st.form("morning_checkin"):
        emotion = st.radio("오늘의 감정 또는 기분", options, horizontal=True)
        st.write(f"**오늘의 질문:** {settings.get('question', DEFAULT_QUESTION)}")
        answer = st.text_input("짧게 답해 주세요")
        submitted = st.form_submit_button("제출하기", type="primary")
    if submitted:
        if not emotion or not answer.strip():
            st.error("감정과 답변을 모두 입력해 주세요.")
            return
        append_csv(
            RESPONSES_FILE,
            {
                "date": date.today().isoformat(),
                "student_id": student["student_id"],
                "name": student["name"],
                "emotion": emotion,
                "answer": answer.strip(),
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
            },
            ["date", "student_id", "name", "emotion", "answer", "submitted_at"],
        )
        st.success("제출되었습니다. 오늘도 응원합니다!")


def render_teacher(settings, students):
    st.header("👩‍🏫 교사용 관리 화면")
    st.subheader("오늘의 질문")
    question = st.text_area("학생에게 보일 질문", settings.get("question", DEFAULT_QUESTION))

    st.subheader("감정 아이콘 보이기/숨기기")
    all_emotions = POSITIVE_EMOTIONS + NEGATIVE_EMOTIONS
    selected = st.multiselect(
        "학생 화면에 보일 감정을 선택하세요",
        [label for _, label in all_emotions],
        default=settings.get("visible_emotions", [label for _, label in all_emotions]),
        format_func=lambda label: next(f"{emoji} {label}" for emoji, item_label in all_emotions if item_label == label),
    )
    if st.button("질문과 감정 설정 저장", type="primary"):
        settings.update({"question": question.strip() or DEFAULT_QUESTION, "visible_emotions": selected})
        save_json(SETTINGS_FILE, settings)
        st.success("저장되었습니다.")

    st.subheader("우리반 명단 업로드")
    uploaded = st.file_uploader("학번, 이름 컬럼이 있는 CSV 또는 XLSX 파일", type=["csv", "xlsx"])
    if uploaded and st.button("명단 가져오기"):
        try:
            students = import_students(uploaded)
            st.success(f"{len(students)}명의 학생을 가져왔습니다.")
        except ValueError as exc:
            st.error(str(exc))

    st.subheader("날짜별 답변 및 상담 기록")
    selected_date = st.date_input("조회 날짜", date.today()).isoformat()
    responses = [r for r in read_csv(RESPONSES_FILE) if r.get("date") == selected_date]
    notes = {(n["date"], n["student_id"]): n for n in read_csv(COUNSELING_FILE)}
    for student in students:
        row = next((r for r in responses if r.get("student_id") == student["student_id"]), {})
        with st.expander(f"{student['student_id']} {student['name']} - {row.get('emotion', '미제출')}"):
            st.write("답변:", row.get("answer", ""))
            key = (selected_date, student["student_id"])
            note = st.text_area("상담 내용", notes.get(key, {}).get("counseling_note", ""), key=f"note-{selected_date}-{student['student_id']}")
            if st.button("상담 내용 저장", key=f"save-{selected_date}-{student['student_id']}"):
                upsert_counseling(selected_date, student, note)
                st.success("상담 내용이 저장되었습니다.")

    st.download_button("답변 CSV 다운로드", to_csv(responses), file_name=f"responses-{selected_date}.csv", mime="text/csv")


def upsert_counseling(selected_date, student, note):
    rows = read_csv(COUNSELING_FILE)
    rows = [r for r in rows if not (r.get("date") == selected_date and r.get("student_id") == student["student_id"])]
    rows.append({"date": selected_date, "student_id": student["student_id"], "name": student["name"], "counseling_note": note, "updated_at": datetime.now().isoformat(timespec="seconds")})
    write_csv(COUNSELING_FILE, rows, ["date", "student_id", "name", "counseling_note", "updated_at"])


def to_csv(rows):
    if not rows:
        return "date,student_id,name,emotion,answer,submitted_at\n".encode("utf-8-sig")
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


ensure_data_files()
st.set_page_config(page_title="아침 마음 출석", page_icon="🌞", layout="wide")
st.title("아침 등교 QR 마음 출석")
settings = load_json(SETTINGS_FILE, {})
students = load_json(STUDENTS_FILE, [])
page = st.sidebar.radio("화면 선택", ["학생용", "교사용"])
st.sidebar.info("QR 코드는 이 앱의 학생용 주소를 생성해 배포하면 됩니다.")
if page == "학생용":
    render_student(settings, students)
else:
    render_teacher(settings, students)
