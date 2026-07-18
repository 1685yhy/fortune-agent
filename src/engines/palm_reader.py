"""CV Palm Reading Engine — palm line detection and analysis.

D4: Uses OpenCV image processing to extract palm lines (生命线/智慧线/
感情线/命运线) from hand photos and map them to palm reading knowledge.

Pure CPU, no GPU needed. ~200ms per palm.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
import numpy as np
import cv2


@dataclass
class PalmMetrics:
    """Extracted palm features."""
    life_line: Dict = field(default_factory=dict)    # {length, depth, breaks, curve}
    wisdom_line: Dict = field(default_factory=dict)   # {length, depth, angle}
    feeling_line: Dict = field(default_factory=dict)   # {length, depth, curve}
    fate_line: Dict = field(default_factory=dict)      # {length, depth, breaks}
    palm_shape: str = ""
    palm_color: str = ""
    finger_type: str = ""
    hand_type: str = ""  # left/right
    special_patterns: List[str] = field(default_factory=list)


class PalmReader:
    """OpenCV-based palm line extraction and analysis.

    Pipeline:
    1. Skin detection (HSV color space)
    2. Edge detection (Canny)
    3. Line extraction (HoughLinesP)
    4. Line classification (position + angle → line type)
    5. RAG lookup + report generation
    """

    def analyze(self, image_path: str) -> Optional[PalmMetrics]:
        img = cv2.imread(image_path)
        if img is None:
            return None

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 1. Skin detection (HSV)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lower = np.array([0, 20, 70], dtype=np.uint8)
        upper = np.array([20, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.GaussianBlur(mask, (5, 5), 0)

        # Check if hand is detected (>10% of image is skin)
        skin_ratio = np.sum(mask > 0) / (h * w)
        if skin_ratio < 0.05:
            return None  # No hand detected

        # 2. Edge detection on masked region
        edges = cv2.Canny(mask, 50, 150)

        # 3. Line detection
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50,
                                minLineLength=min(h, w) // 8,
                                maxLineGap=min(h, w) // 15)

        metrics = PalmMetrics()
        if lines is None:
            return metrics  # Return with empty lines

        # 4. Classify detected lines by position and angle
        hand_center_y = h // 2
        palm_top = int(h * 0.25)
        palm_bottom = int(h * 0.75)

        life_lines = []    # Top-left, curved → thumb area
        wisdom_lines = []  # Mid, horizontal → center
        feeling_lines = [] # Upper-mid, horizontal → below fingers
        fate_lines = []    # Vertical, center → wrist to middle finger

        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            angle = np.abs(np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi)
            mid_y = (y1 + y2) / 2
            mid_x = (x1 + x2) / 2

            item = {"length": length, "angle": angle, "mid_x": mid_x, "mid_y": mid_y}

            if angle < 20 and palm_top < mid_y < palm_top + h * 0.15:
                feeling_lines.append(item)
            elif angle < 25 and palm_top + h * 0.1 < mid_y < hand_center_y:
                wisdom_lines.append(item)
            elif 30 < angle < 80 and mid_x < w * 0.4:
                life_lines.append(item)
            elif 60 < angle < 120:
                fate_lines.append(item)

        # Sort by length, take longest
        life_lines.sort(key=lambda x: x["length"], reverse=True)
        wisdom_lines.sort(key=lambda x: x["length"], reverse=True)
        feeling_lines.sort(key=lambda x: x["length"], reverse=True)
        fate_lines.sort(key=lambda x: x["length"], reverse=True)

        # Classify life line
        if life_lines:
            ll = life_lines[0]
            metrics.life_line = {
                "detected": True,
                "length_ratio": round(ll["length"] / max(h, 1), 2),
                "depth": "深" if ll["length"] > h * 0.4 else "浅",
                "curve": "弯" if ll["angle"] > 50 else "直",
            }
        else:
            metrics.life_line = {"detected": False}

        # Classify wisdom line
        if wisdom_lines:
            wl = wisdom_lines[0]
            metrics.wisdom_line = {
                "detected": True,
                "length_ratio": round(wl["length"] / max(w, 1), 2),
                "angle": round(wl["angle"], 1),
            }
        else:
            metrics.wisdom_line = {"detected": False}

        # Classify feeling line
        if feeling_lines:
            fl = feeling_lines[0]
            metrics.feeling_line = {
                "detected": True,
                "length_ratio": round(fl["length"] / max(w, 1), 2),
                "curve": "弯" if fl["angle"] < 10 else "直",
            }
        else:
            metrics.feeling_line = {"detected": False}

        # Classify fate line
        if fate_lines:
            ft = fate_lines[0]
            metrics.fate_line = {
                "detected": True,
                "length_ratio": round(ft["length"] / max(h, 1), 2),
                "depth": "深" if ft["length"] > h * 0.3 else "浅",
            }
        else:
            metrics.fate_line = {"detected": False}

        # Hand type
        aspect = w / max(h, 1)
        if aspect > 0.7:
            metrics.palm_shape = "方掌"
        elif aspect < 0.55:
            metrics.palm_shape = "长掌"
        else:
            metrics.palm_shape = "标准掌"

        # Palm color
        skin_region = img[int(h*0.3):int(h*0.7), int(w*0.2):int(w*0.8)]
        if skin_region.size > 0:
            avg_color = np.mean(skin_region, axis=(0, 1))
            r, g, b = avg_color[2], avg_color[1], avg_color[0]
            if r > 180:
                metrics.palm_color = "红润"
            elif r < 120:
                metrics.palm_color = "偏暗"
            else:
                metrics.palm_color = "正常"

        # Finger ratio (simplified)
        finger_area = mask[int(h*0.05):int(h*0.25), :]
        finger_ratio = np.sum(finger_area > 0) / max(finger_area.size, 1)
        metrics.finger_type = "修长" if finger_ratio < 0.15 else "粗短" if finger_ratio > 0.3 else "适中"

        # Special patterns
        if len(life_lines) > 2:
            metrics.special_patterns.append("双生命线")
        if not life_lines:
            metrics.special_patterns.append("生命线模糊")
        if len(fate_lines) > 1:
            metrics.special_patterns.append("双命运线")
        if feeling_lines and wisdom_lines and life_lines:
            fl = feeling_lines[0]
            wl = wisdom_lines[0]
            ll = life_lines[0]
            if abs(fl["mid_y"] - wl["mid_y"]) < 10:
                metrics.special_patterns.append("感情线与智慧线交汇")

        return metrics


def generate_palm_report(metrics: PalmMetrics, retriever=None, api_key: str = "",
                         personality: str = "sassy") -> str:
    """Generate palm reading report from CV measurements + RAG."""
    lines_text = "## 掌纹检测\n"
    for name, data in [("生命线", metrics.life_line), ("智慧线", metrics.wisdom_line),
                        ("感情线", metrics.feeling_line), ("命运线", metrics.fate_line)]:
        if data.get("detected"):
            details = ", ".join(f"{k}={v}" for k, v in data.items() if k != "detected")
            lines_text += f"- ✅ {name}: {details}\n"
        else:
            lines_text += f"- ❌ {name}: 未检测到\n"

    lines_text += f"\n掌型: {metrics.palm_shape} | 掌色: {metrics.palm_color} | 指型: {metrics.finger_type}\n"

    if metrics.special_patterns:
        lines_text += f"特殊纹路: {', '.join(metrics.special_patterns)}\n"

    # RAG lookup
    refs = []
    if retriever:
        for term in ["手相 生命线", "手相 智慧线", "手相 感情线"]:
            refs.extend(retriever.search(term, top_k=2)[:1])

    refs_text = ""
    if refs:
        refs_text = "\n📖 古籍依据:\n" + "\n".join(f"- {r.content[:200]}" for r in refs[:3])

    if api_key:
        try:
            import httpx
            prompt = f"精通手相学的专家。根据检测数据生成150-300字解读。\n{lines_text}\n{refs_text}"
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 400, "temperature": 0.7}, timeout=30.0)
            llm = resp.json()["choices"][0]["message"]["content"]
            return f"✋ **手相分析报告**\n\n{llm}\n\n{lines_text}\n{refs_text}"
        except Exception:
            pass
    return f"✋ **手相分析报告**\n\n{lines_text}\n{refs_text}"
