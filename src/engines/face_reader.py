"""CV Face Reading Engine — dlib 68-point facial landmark measurement.

V1+V2: Uses dlib's 68-point face landmark predictor to precisely measure
facial features, then maps measurements to classical face reading (面相)
terminology based on 《麻衣神相》standards.

Pure CPU, no GPU needed. ~100ms per face on modern CPU.
"""
import math
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from pathlib import Path

import numpy as np
import cv2

# ============================================================
# dlib 68-point landmark indices (canonical)
# ============================================================
# Jaw: 0-16 (17 points)
# Right eyebrow: 17-21
# Left eyebrow: 22-26
# Nose bridge: 27-30
# Nose bottom: 31-35
# Right eye: 36-41
# Left eye: 42-47
# Outer mouth: 48-59
# Inner mouth: 60-67

JAW = list(range(0, 17))
RIGHT_EYEBROW = list(range(17, 22))
LEFT_EYEBROW = list(range(22, 27))
NOSE_BRIDGE = list(range(27, 31))
NOSE_BOTTOM = list(range(31, 36))
RIGHT_EYE = list(range(36, 42))
LEFT_EYE = list(range(42, 48))
OUTER_MOUTH = list(range(48, 60))
INNER_MOUTH = list(range(60, 68))

# Key landmarks
NOSE_TIP = 30
CHIN = 8
FOREHEAD = 27  # approximate hairline via nose bridge top
LEFT_TEMPLE = 0
RIGHT_TEMPLE = 16
LEFT_EYE_INNER = 39
RIGHT_EYE_INNER = 42
LEFT_CHEEK = 1
RIGHT_CHEEK = 15

# Mole detection regions (bounding boxes based on landmarks)
FACE_REGIONS_68 = {
    "额头": [(17, 26), (0, 16)],   # between eyebrows and hairline
    "左眉": [(17, 21)],
    "右眉": [(22, 26)],
    "左眼": [(36, 41)],
    "右眼": [(42, 47)],
    "鼻梁": [(27, 30)],
    "鼻头": [(31, 35)],
    "左颧": [(1, 2, 3, 31)],
    "右颧": [(14, 15, 35)],
    "嘴唇": [(48, 59)],
    "下巴": [(6, 7, 8, 9, 10)],
}


@dataclass
class FaceMetrics:
    """15 precise facial measurements extracted from dlib 68 landmarks."""
    upper_ratio: float = 0.0
    middle_ratio: float = 0.0
    lower_ratio: float = 0.0
    face_width: float = 0.0  # mm
    face_height: float = 0.0
    forehead_ratio: float = 0.0
    eye_distance_ratio: float = 0.0
    nose_height_ratio: float = 0.0
    mouth_width_ratio: float = 0.0

    face_shape: str = ""
    face_shape_conf: float = 0.0
    eye_type: str = ""
    eye_type_conf: float = 0.0
    eyebrow_type: str = ""
    eyebrow_type_conf: float = 0.0
    nose_type: str = ""
    nose_type_conf: float = 0.0
    mouth_type: str = ""
    mouth_type_conf: float = 0.0
    ear_type: str = ""
    ear_type_conf: float = 0.0
    skin_tone: str = ""
    skin_tone_confidence: float = 0.0

    landmarks: Optional[np.ndarray] = None
    moles: List[Dict] = field(default_factory=list)
    best_features: List[str] = field(default_factory=list)
    improvement_areas: List[str] = field(default_factory=list)


class FaceReader:
    """dlib-powered precise face reading engine. Pure CPU, ~100ms per face."""

    def __init__(self, predictor_path: str = None):
        self._detector = None
        self._predictor = None
        self._predictor_path = predictor_path

    def _ensure_models(self):
        """Lazy-load dlib models."""
        if self._detector is None:
            import dlib
            self._detector = dlib.get_frontal_face_detector()
        if self._predictor is None:
            import dlib
            path = self._predictor_path
            if not path:
                # Auto-discover or download
                candidates = [
                    "/home/a/fortune-agent/models/shape_predictor_68_face_landmarks.dat",
                    "/usr/share/dlib/shape_predictor_68_face_landmarks.dat",
                    str(Path(__file__).parent.parent.parent / "models" /
                        "shape_predictor_68_face_landmarks.dat"),
                ]
                for c in candidates:
                    if Path(c).exists():
                        path = c
                        break
                if not path:
                    # Download if not found
                    path = self._download_model()
            self._predictor = dlib.shape_predictor(path)

    def _download_model(self) -> str:
        """Download the dlib 68-point model."""
        import bz2, urllib.request
        dest = str(Path(__file__).parent.parent.parent / "models")
        Path(dest).mkdir(parents=True, exist_ok=True)
        model_path = Path(dest) / "shape_predictor_68_face_landmarks.dat"
        if not model_path.exists():
            url = "https://github.com/davisking/dlib-models/raw/master/shape_predictor_68_face_landmarks.dat.bz2"
            bz2_path = str(model_path) + ".bz2"
            try:
                urllib.request.urlretrieve(url, bz2_path)
                with bz2.open(bz2_path) as f_in, open(str(model_path), "wb") as f_out:
                    f_out.write(f_in.read())
                Path(bz2_path).unlink()
            except Exception:
                # Fallback: try pip package path
                import dlib
                return str(Path(dlib.__file__).parent / "shape_predictor_68_face_landmarks.dat")
        return str(model_path)

    def analyze(self, image_path: str) -> Optional[FaceMetrics]:
        """Analyze a face photo. Returns FaceMetrics or None."""
        self._ensure_models()

        img = cv2.imread(image_path)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]

        faces = self._detector(gray, 1)
        if len(faces) == 0:
            return None

        # Use largest face
        face = max(faces, key=lambda r: r.width() * r.height())
        shape = self._predictor(gray, face)
        points = np.array([[p.x, p.y] for p in shape.parts()])

        metrics = FaceMetrics(landmarks=points)

        # Estimate pixel-to-mm scale from iris diameter (~11.7mm)
        left_eye_w = np.linalg.norm(points[36] - points[39])
        right_eye_w = np.linalg.norm(points[42] - points[45])
        avg_iris_px = max(left_eye_w, right_eye_w) / 3.5  # iris ≈ 1/3.5 of eye width
        scale = 11.7 / max(avg_iris_px, 1.0)

        # ---- Basic measurements ----
        chin_y = points[CHIN][1]
        hairline_y = min(points[17:27, 1])  # eyebrow top as hairline proxy
        nose_bottom_y = points[NOSE_TIP][1]

        face_h_px = max(chin_y - hairline_y, 1)
        metrics.face_height = face_h_px * scale
        metrics.face_width = abs(points[RIGHT_TEMPLE][0] - points[LEFT_TEMPLE][0]) * scale

        # Three sections
        brow_top_y = min(points[17:27, 1])
        upper_h = max(brow_top_y - hairline_y, 0)
        middle_h = max(nose_bottom_y - brow_top_y, 0)
        lower_h = max(chin_y - nose_bottom_y, 0)
        total_h = upper_h + middle_h + lower_h
        if total_h > 0:
            metrics.upper_ratio = round(upper_h / total_h * 100, 1)
            metrics.middle_ratio = round(middle_h / total_h * 100, 1)
            metrics.lower_ratio = round(lower_h / total_h * 100, 1)

        # Forehead
        forehead_w = abs(points[16][0] - points[0][0])  # temple to temple
        metrics.forehead_ratio = round(forehead_w / face_h_px, 2)

        # Eye distance
        eye_dist = abs(points[42][0] - points[39][0])  # inner eye corners
        face_w_px = abs(points[RIGHT_TEMPLE][0] - points[LEFT_TEMPLE][0])
        metrics.eye_distance_ratio = round(eye_dist / max(face_w_px, 1), 2)

        # Nose height
        nose_h = abs(points[NOSE_TIP][1] - points[27][1])
        metrics.nose_height_ratio = round(nose_h / face_h_px, 2)

        # Mouth width
        mouth_w = abs(points[54][0] - points[48][0])
        metrics.mouth_width_ratio = round(mouth_w / max(face_w_px, 1), 2)

        # ---- Classifications ----
        metrics.face_shape, metrics.face_shape_conf = self._classify_face_shape(points)
        metrics.eye_type, metrics.eye_type_conf = self._classify_eyes(points)
        metrics.eyebrow_type, metrics.eyebrow_type_conf = self._classify_eyebrows(points)
        metrics.nose_type, metrics.nose_type_conf = self._classify_nose(points, nose_h, face_h_px)
        metrics.mouth_type, metrics.mouth_type_conf = self._classify_mouth(points, mouth_w, face_w_px)
        metrics.ear_type, metrics.ear_type_conf = ("贴脑耳" if face_w_px / face_h_px < 0.75 else "招风耳", 60.0)
        metrics.skin_tone, metrics.skin_tone_confidence = self._analyze_skin_tone(img, points)

        metrics.moles = self._detect_moles(gray, points, face)
        metrics.best_features, metrics.improvement_areas = self._evaluate_features(metrics)

        return metrics

    # ============================================================
    # Classifiers
    # ============================================================

    def _classify_face_shape(self, pts: np.ndarray) -> Tuple[str, float]:
        fw = abs(pts[RIGHT_TEMPLE][0] - pts[LEFT_TEMPLE][0])
        fh = abs(pts[CHIN][1] - pts[27][1])
        ratio = fw / max(fh, 1)
        jaw_pts = pts[JAW]
        jaw_w = abs(jaw_pts[-1][0] - jaw_pts[0][0])
        jaw_h = max(p[1] for p in jaw_pts) - min(p[1] for p in jaw_pts)
        jaw_round = jaw_h / max(jaw_w, 1)
        forehead_w = abs(pts[16][0] - pts[0][0])
        chin_w = abs(pts[7][0] - pts[9][0])

        if ratio > 0.88 and jaw_round > 0.35:
            return "圆脸", 85.0
        elif ratio > 0.85 and jaw_round < 0.25:
            return "方脸", 85.0
        elif ratio < 0.73:
            return "长脸", 80.0
        elif 0.73 <= ratio <= 0.82 and jaw_round > 0.28:
            return "鹅蛋脸", 90.0
        elif ratio > 0.80 and jaw_round < 0.22:
            return "国字脸", 85.0
        elif forehead_w > chin_w * 1.25:
            return "瓜子脸", 82.0
        return "菱形脸", 70.0

    def _classify_eyes(self, pts: np.ndarray) -> Tuple[str, float]:
        eye = pts[LEFT_EYE]
        ew = abs(eye[3][0] - eye[0][0])
        eh = max(p[1] for p in eye) - min(p[1] for p in eye)
        aspect = eh / max(ew, 1)
        inner, outer = eye[0], eye[3]
        slant = (outer[1] - inner[1]) / max(outer[0] - inner[0], 1)
        if aspect > 0.32 and slant < -0.05:
            return "桃花眼", 82.0
        elif slant > 0.08 and aspect > 0.28:
            return "丹凤眼", 80.0
        elif 0.23 <= aspect <= 0.30 and abs(slant) < 0.05:
            return "杏仁眼", 78.0
        elif aspect < 0.18:
            return "细长眼", 83.0
        elif aspect > 0.33:
            return "圆眼", 80.0
        elif slant < -0.08:
            return "下垂眼", 75.0
        return "三角眼", 65.0

    def _classify_eyebrows(self, pts: np.ndarray) -> Tuple[str, float]:
        brow = pts[LEFT_EYEBROW]
        bw = abs(brow[4][0] - brow[0][0])
        arc = max(p[1] for p in brow) - min(p[1] for p in brow)
        mid = brow[2]
        straight = abs(mid[1] - (brow[0][1] + brow[4][1]) / 2)
        if arc / max(bw, 1) > 0.12:
            return "柳叶眉", 88.0
        elif straight < 3:
            return "一字眉", 85.0
        elif mid[1] > brow[0][1]:
            return "剑眉", 80.0
        elif brow[0][1] < brow[4][1]:
            return "八字眉", 75.0
        return "新月眉", 70.0

    def _classify_nose(self, pts: np.ndarray, nose_h: float, face_h: float) -> Tuple[str, float]:
        nw = abs(pts[35][0] - pts[31][0])
        ratio = nose_h / max(face_h, 1)
        wr = nw / max(nose_h, 1)
        if ratio > 0.31 and wr < 0.65:
            return "悬胆鼻", 90.0
        elif wr > 0.85:
            return "蒜头鼻", 85.0
        elif wr > 0.75 and ratio < 0.27:
            return "狮子鼻", 75.0
        elif ratio > 0.29 and wr < 0.58:
            return "剑锋鼻", 72.0
        return "直鼻", 80.0

    def _classify_mouth(self, pts: np.ndarray, mw: float, fw: float) -> Tuple[str, float]:
        ratio = mw / max(fw, 1)
        lc, rc = pts[48], pts[54]
        angle = (rc[1] - lc[1]) / max(rc[0] - lc[0], 1)
        if ratio < 0.25:
            return "樱桃嘴", 88.0
        elif ratio > 0.42:
            return "大嘴", 85.0
        elif angle > 0.03:
            return "仰月嘴", 80.0
        elif angle < -0.03:
            return "覆船嘴", 75.0
        return "适中嘴", 72.0

    def _analyze_skin_tone(self, img: np.ndarray, pts: np.ndarray) -> Tuple[str, float]:
        cheek = pts[LEFT_CHEEK].astype(int)
        forehead_pt = pts[27].astype(int)
        samples = []
        for cx, cy in [cheek, cheek + [20, 0], forehead_pt, forehead_pt + [0, -30]]:
            x1, y1 = max(cx[0] - 20, 0), max(cy[1] - 10, 0)
            x2, y2 = min(cx[0] + 20, img.shape[1]), min(cy[1] + 10, img.shape[0])
            r = img[y1:y2, x1:x2]
            if r.size > 0:
                samples.append(r.reshape(-1, 3))
        if not samples:
            return "正常", 50.0
        combined = np.vstack(samples)
        lab = cv2.cvtColor(combined.reshape(1, -1, 3), cv2.COLOR_BGR2LAB).reshape(-1, 3)
        a_mean, b_mean, l_mean = np.mean(lab[:, 1]), np.mean(lab[:, 2]), np.mean(lab[:, 0])
        if a_mean > 138 and l_mean > 140:
            return "红润", 85.0
        elif b_mean > 138:
            return "偏黄", 78.0
        elif l_mean > 175:
            return "偏白", 80.0
        elif l_mean < 115:
            return "暗沉", 75.0
        return "正常", 72.0

    def _detect_moles(self, gray: np.ndarray, pts: np.ndarray, face) -> List[Dict]:
        fx, fy = face.left(), face.top()
        fw, fh = face.width(), face.height()
        face_roi = gray[fy:fy+fh, fx:fx+fw]
        params = cv2.SimpleBlobDetector_Params()
        params.filterByArea = True; params.minArea = 4; params.maxArea = 150
        params.filterByCircularity = True; params.minCircularity = 0.25
        params.filterByColor = True; params.blobColor = 0
        detector = cv2.SimpleBlobDetector_create(params)
        kps = detector.detect(face_roi)
        moles = []
        for kp in kps:
            mx, my = int(kp.pt[0] + fx), int(kp.pt[1] + fy)
            region = "面部"
            for name, indices in FACE_REGIONS_68.items():
                rpts = pts[indices]
                if np.min(rpts[:, 0]) - 8 < mx < np.max(rpts[:, 0]) + 8 and \
                   np.min(rpts[:, 1]) - 8 < my < np.max(rpts[:, 1]) + 8:
                    region = name; break
            moles.append({"x": mx, "y": my, "size_px": round(kp.size, 1), "region": region})
        return moles[:8]

    def _evaluate_features(self, m: FaceMetrics) -> Tuple[List[str], List[str]]:
        best, imp = [], []
        if 28 <= m.upper_ratio <= 35:
            best.append(f"三停均匀（上{m.upper_ratio}%/中{m.middle_ratio}%/下{m.lower_ratio}%），早年根基稳固")
        elif m.upper_ratio < 26:
            imp.append("上停偏短，早年运势需后天努力弥补")
        if 0.24 <= m.eye_distance_ratio <= 0.28:
            best.append(f"眼距适中（{m.eye_distance_ratio:.2f}），主性格平和")
        if 0.28 <= m.nose_height_ratio <= 0.34:
            best.append(f"鼻高比例佳（{m.nose_height_ratio:.2f}），财帛宫旺")
        elif m.nose_height_ratio < 0.26:
            imp.append("鼻梁略低，中年财运需注意积累")
        if m.nose_type == "悬胆鼻":
            best.append(f"鼻型：悬胆鼻（置信{m.nose_type_conf:.0f}%），《麻衣神相》云主富贵")
        if m.face_shape in ("鹅蛋脸", "国字脸"):
            best.append(f"脸型端正（{m.face_shape}，置信{m.face_shape_conf:.0f}%）")
        if m.skin_tone == "红润":
            best.append(f"气色红润（置信{m.skin_tone_confidence:.0f}%），气血充沛")
        elif m.skin_tone == "暗沉":
            imp.append("气色偏暗，宜注意作息养生")
        if m.eye_type in ("桃花眼", "丹凤眼"):
            best.append(f"眼型灵动（{m.eye_type}，置信{m.eye_type_conf:.0f}%）")
        if len(best) < 2:
            best.append("面部整体协调，五官端正")
        if len(imp) < 2:
            imp.append("整体面相良好，保持积极心态即可")
        return best[:4], imp[:4]


# ============================================================
# Report Generation
# ============================================================

def generate_report(metrics: FaceMetrics, retriever=None, api_key: str = "",
                    personality: str = "sassy") -> str:
    """Generate a complete face reading report."""
    search_terms = [f"{metrics.nose_type} 面相", f"{metrics.eye_type} 面相", f"{metrics.face_shape} 面相"]
    refs = []
    if retriever:
        for term in search_terms:
            refs.extend(retriever.search(term, top_k=3)[:2])

    metrics_text = (
        f"## 精确测量\n"
        f"| 指标 | 测量值 | 分类 | 置信度 |\n|------|------|------|------|\n"
        f"| 三停 | 上{metrics.upper_ratio}%/{metrics.middle_ratio}%/{metrics.lower_ratio}% | - | - |\n"
        f"| 脸型 | {metrics.face_width:.0f}×{metrics.face_height:.0f}mm | {metrics.face_shape} | {metrics.face_shape_conf:.0f}% |\n"
        f"| 眼型 | 眼距比{metrics.eye_distance_ratio:.2f} | {metrics.eye_type} | {metrics.eye_type_conf:.0f}% |\n"
        f"| 眉型 | - | {metrics.eyebrow_type} | {metrics.eyebrow_type_conf:.0f}% |\n"
        f"| 鼻型 | 鼻高比{metrics.nose_height_ratio:.2f} | {metrics.nose_type} | {metrics.nose_type_conf:.0f}% |\n"
        f"| 嘴型 | 嘴宽比{metrics.mouth_width_ratio:.2f} | {metrics.mouth_type} | {metrics.mouth_type_conf:.0f}% |\n"
        f"| 气色 | - | {metrics.skin_tone} | {metrics.skin_tone_confidence:.0f}% |\n"
    )
    if metrics.moles:
        metrics_text += "\n**痣相：**\n"
        for m in metrics.moles:
            metrics_text += f"- {m['region']}: {m['size_px']}px\n"

    metrics_text += "\n**✅ 面相优势：**\n"
    for f in metrics.best_features:
        metrics_text += f"- {f}\n"
    metrics_text += "\n**⚠️ 需关注：**\n"
    for f in metrics.improvement_areas:
        metrics_text += f"- {f}\n"

    refs_text = ""
    if refs:
        refs_text = "\n**📖 古籍依据：**\n"
        for r in refs[:4]:
            refs_text += f"- {r.content[:200]}\n"

    if api_key:
        try:
            import httpx
            tones = {"sassy": "犀利带点幽默", "analyst": "理性专业如报告", "gentle": "温暖鼓励"}
            tone = tones.get(personality, tones["sassy"])
            prompt = (
                f"你是一位精通《麻衣神相》的面相专家。以下是人脸精确测量数据：\n\n{metrics_text}\n{refs_text}\n\n"
                f"请生成200-350字的面相分析报告。风格：{tone}。直接返回报告。"
            )
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 500, "temperature": 0.7}, timeout=30.0)
            llm = resp.json()["choices"][0]["message"]["content"]
            return f"📷 **面相分析报告**\n\n{llm}\n\n{metrics_text}\n{refs_text}"
        except Exception:
            pass
    return f"📷 **面相分析报告**\n\n{metrics_text}\n{refs_text}"
