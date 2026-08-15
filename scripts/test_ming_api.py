"""AI 取名 API+引擎测试(TestClient+临时 DB+假 token+LLM/RAG mock)
```
运行：.venv/bin/python3 scripts/test_ming_api.py
退出码：0=全部通过；1=有失败
```
覆盖：401/生成 5 名字段完整/第 4、5 名截断(只给分数)/五维权重正确性/
评分器确定性/LLM mock/LLM 降级(规则库兜底)/出处 RAG mock(命中有、未命中无)/
付费边界(未购 403/已购 200/会员 200/体验模式全免费)/名笺收藏幂等/日额度 429/
T4 跟进：降级种子去重(同日两次生成名不同)/成人两档定价(ming_report 19.9
+ming_report_pro 29.9, 未购 403, pro 覆盖宝宝档, 低档不覆盖成人)/成人免费现名诊断。
"""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["EXPERIENCE_MODE"] = ""
os.environ["MING_DAILY_LIMIT"] = "100"   # 常规测试额度放宽(额度单独测)

from starlette.testclient import TestClient

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"PASS: {name}")


# ── 独立临时 DB ────────────────────────────────────────────────
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path

import src.engines.ming as ming_eng
import src.api.ming as ming_mod
from src.main import app

# 注入临时 DAO(仿 test_qian_api.py: qian_mod._dao = 临时实例)
from src.storage.ming_dao import MingDAO
_test_dao = MingDAO(dao_mod.get_conn())
ming_mod._ming_dao = _test_dao
ming_mod._bazi_engine = None   # 引擎自实例化(lunar-python 可用)

client = TestClient(app)

# ── 假 token ───────────────────────────────────────────────────
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
UID = "ming-test-user"
UID2 = "ming-test-user-b"
UID3 = "ming-test-user-quota"
TOKEN = _auth.create_user_token(UID)
TOKEN2 = _auth.create_user_token(UID2)
TOKEN3 = _auth.create_user_token(UID3)
h = {"Authorization": f"Bearer {TOKEN}"}
h2 = {"Authorization": f"Bearer {TOKEN2}"}
h3 = {"Authorization": f"Bearer {TOKEN3}"}

# ── 依赖 stub ──────────────────────────────────────────────────

class _MemberStub:
    """member_dao stub: 可配置已购/会员。"""

    def __init__(self):
        self.purchases = {}          # uid -> set(product_id)
        self.members = {}            # uid -> plan

    def get_user_purchase(self, uid, product_id):
        if self.purchases.get(uid) and product_id in self.purchases[uid]:
            return {"plan": product_id, "status": "paid"}
        return None

    def get_membership(self, uid):
        return {"plan": self.members.get(uid, "free")}


class _Hit:
    def __init__(self, text, source):
        self.text = text
        self.source = source


class _RetrieverStub:
    """RAG stub: search 返回配置好的 hits。"""

    def __init__(self, hits=None, fail=False):
        self.hits = hits or []
        self.fail = fail

    def search(self, query, **kw):
        if self.fail:
            raise RuntimeError("faiss down")
        return self.hits


class _BadLLM:
    """LLM stub: 固定抛错 → 触发规则降级。"""

    def __call__(self, *a, **k):
        raise RuntimeError("llm down")


class _FixedLLM:
    """LLM stub: 返回固定 5 名(原型示例名),用于确定性断言。"""

    def __init__(self, names=None):
        self.names = names or [
            {"given": "云舒", "ping": "如云舒展，一生从容。"},
            {"given": "晚晴", "ping": "雨过天青，心境澄明。"},
            {"given": "砚溪", "ping": "文脉如溪，笔底有光。"},
            {"given": "执月", "ping": "执月在手，清辉自守。"},
            {"given": "松风", "ping": "松风入耳，心自澄明。"},
        ]

    def __call__(self, *a, **k):
        return list(self.names)


def set_llm(fn):
    """替换引擎的 LLM 实现(模块级,generate_names 调用时解析)。"""
    ming_eng._llm_generate_names = fn


def set_retriever(r):
    ming_mod._retriever = r


def set_member(stub):
    ming_mod._member_dao = stub


# ─────────────────────────── 401 ───────────────────────────────
r = client.post("/api/ming/generate", json={"surname": "林"})
check("401 未登录 generate", r.status_code == 401)
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒"})
check("401 未登录 report", r.status_code == 401)

# ─────────────────── 生成 5 名 + 字段 + 截断 ───────────────────
set_retriever(None)
set_member(_MemberStub())
set_llm(_FixedLLM())
os.environ["DEEPSEEK_API_KEY"] = "test-key"  # 让 LLM 路径生效(被 stub 接管)

r = client.post("/api/ming/generate", json={
    "surname": "林", "gender": "女", "style_chips": ["诗意"], "mode": "baby"}, headers=h)
if r.status_code != 200:
    print("DEBUG generate status:", r.status_code, r.text[:400])
check("生成 200", r.status_code == 200)
data = r.json()
check("生成返回 5 名", len(data["names"]) == 5)
for it in data["names"]:
    check(f"名卡字段完整 {it['given']}", (
        it.get("given") and it.get("full") and isinstance(it.get("score"), int)
        and it.get("level") and len(it.get("dims", {})) == 5
        and all(k in it["dims"] for k in ("音形义", "五行", "数理", "笔画", "性别匹配"))))
check("前 3 名含点评(深度解析)", all(it.get("ping") for it in data["names"][:3]))
check("前 3 名含五行补益标签", all("buyi" in it for it in data["names"][:3]))
check("前 3 名未锁定", all(not it["locked"] for it in data["names"][:3]))
check("第 4、5 名锁定(只给分数)", all(it["locked"] for it in data["names"][3:]))
check("第 4、5 名无点评/补益/出处(截断深度解析)",
      all(not it.get("ping") and not it.get("buyi") and not it.get("src")
          for it in data["names"][3:]))
check("按综合分降序", [data["names"][i]["score"] >= data["names"][i + 1]["score"]
      for i in range(4)].count(False) == 0)

# ── 五维权重正确性(30/25/25/10/10) ──
for it in data["names"]:
    d = it["dims"]
    calc = 0.30 * d["音形义"] + 0.25 * d["五行"] + 0.25 * d["数理"] + 0.10 * d["笔画"] + 0.10 * d["性别匹配"]
    check(f"权重加权==综合分 {it['given']} ({calc:.1f} vs {it['score']})",
          abs(calc - it["score"]) <= 1.0)

# ── 评分器确定性(单名输入五维输出,同输入同输出) ──
s1 = ming_eng.score_name("林", "云舒", "女", ["诗意"])
s2 = ming_eng.score_name("林", "云舒", "女", ["诗意"])
check("评分器确定性", s1 == s2)
check("综合分百分制", 0 <= s1["total"] <= 100)

# ── 出处 RAG mock: 命中 → 有出处; 未命中/无检索器 → 无出处 ──
set_retriever(_RetrieverStub(hits=[
    _Hit("云舒天行健，君子以自强不息", "周易"),
    _Hit("晚晴风过竹，疏影入窗纱", "闲情偶寄"),
]))
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "女", "style_chips": ["诗意"]}, headers=h)
src_names = [it["given"] for it in r.json()["names"][:3] if it.get("src")]
check("RAG 命中云舒 → 出处《周易》",
      any(it["given"] == "云舒" and it["src"]["book"] == "周易"
          for it in r.json()["names"][:3] if it.get("src")))
set_retriever(_RetrieverStub(hits=[_Hit("天地玄黄宇宙洪荒", "千字文")]))
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "女", "style_chips": ["诗意"]}, headers=h)
check("RAG 未命中 → 不出处(不编造)",
      all(not it.get("src") for it in r.json()["names"][:3]))
set_retriever(None)
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "女"}, headers=h)
check("无检索器 → 不出处", all(not it.get("src") for it in r.json()["names"][:3]))

# ── LLM 降级: 固定抛错 → 规则字库仍出 5 名 ──
set_llm(_BadLLM())
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "男", "style_chips": ["大气"]}, headers=h)
check("LLM 降级仍返回 5 名", r.status_code == 200 and len(r.json()["names"]) == 5)
check("降级名全为 2 字汉字", all(len(it["given"]) == 2 and all(
    "一" <= ch <= "龥" for ch in it["given"]) for it in r.json()["names"]))

# ── T4 跟进 1: 降级模式同日两次生成(含重新生成)结果不同(种子混入当日已用次数) ──
s1 = ming_mod._daily_seed(UID)
s2 = ming_mod._daily_seed(UID)
check("种子同日递增(当日已用次数混入)", s1 != s2 and s1.startswith(f"{UID}:") and s2.startswith(f"{UID}:"))
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "男", "style_chips": ["大气"]}, headers=h)
r_regen = client.post("/api/ming/generate", json={"surname": "林", "gender": "男", "style_chips": ["大气"]}, headers=h)
names_a = [it["given"] for it in r.json()["names"]]
names_b = [it["given"] for it in r_regen.json()["names"]]
check("降级同日两次生成名不同", r.status_code == 200 and r_regen.status_code == 200
      and names_a != names_b and len(set(names_a) & set(names_b)) < 5)

# ── 性别过滤(规则生成: 男名不出现女向字) ──
for it in r.json()["names"]:
    for ch in it["given"]:
        info = ming_eng.CHAR_LIB.get(ch)
        if info and info["g"] == "f":
            check(f"男名含女向字 {it['given']}", False)
check("男名无女向字", True)

# ── 无 LLM 密钥 → 规则兜底 ──
del os.environ["DEEPSEEK_API_KEY"]
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "女", "style_chips": ["诗意"]}, headers=h)
check("无密钥规则兜底 5 名", r.status_code == 200 and len(r.json()["names"]) == 5)

# ── T4 跟进 3: 成人免费现名诊断(免费档) ──
r = client.post("/api/ming/generate", json={
    "surname": "林", "gender": "女", "mode": "adult", "current_name": "晚晴",
    "style_chips": ["诗意"]}, headers=h)
check("成人免费生成 200", r.status_code == 200)
diag = r.json().get("current_name_issues")
check("成人免费响应含 current_name_issues", isinstance(diag, list) and len(diag) >= 1)
check("现名诊断 ≤5 条", len(diag) <= 5)
check("现名诊断为中文简评", all(isinstance(x, str) and x.strip() for x in diag))
r = client.post("/api/ming/generate", json={
    "surname": "林", "gender": "女", "style_chips": ["诗意"]}, headers=h)
check("宝宝响应不含现名诊断", "current_name_issues" not in r.json())

# ── 付费边界 ──
stub = _MemberStub()
set_member(stub)
os.environ["EXPERIENCE_MODE"] = ""
set_llm(_FixedLLM())
os.environ["DEEPSEEK_API_KEY"] = "test-key"

# 未购 → 403
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女"}, headers=h)
check("深度报告未购 403", r.status_code == 403)

# 已购 ming_report → 200
stub.purchases[UID] = {"ming_report"}
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女",
                                          "style_chips": ["诗意"]}, headers=h)
check("已购 ming_report → 200", r.status_code == 200)
rep = r.json()["report"]
check("报告含契合度矩阵", "buyi_matrix" in rep["name_analysis"]
      and "rows" in rep["name_analysis"]["buyi_matrix"])
check("报告含备选 15 名", len(rep["extras"]) == 15)
check("报告含名笺落款", rep["seal"]["recommended"] == "林云舒")
check("报告含合规脚注", "footnote" in rep)

# 无生辰 → 报告无八字章节(不编造)
check("无生辰不出八字概览", "bazi" not in rep)

# 生辰完整 → 八字概览+五行分布+用神
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "style_chips": ["诗意"],
    "birthYear": 2026, "birthMonth": 5, "birthDay": 20}, headers=h)
check("带生辰报告 200", r.status_code == 200)
rep = r.json()["report"]
check("八字概览四柱", "bazi" in rep and len(rep["bazi"]["pillars"]) == 4)
check("八字五行分布", rep["bazi"]["wuxing"]["counts"] == {
    "金": rep["bazi"]["wuxing"]["counts"]["金"],
    "木": rep["bazi"]["wuxing"]["counts"]["木"],
    "水": rep["bazi"]["wuxing"]["counts"]["水"],
    "火": rep["bazi"]["wuxing"]["counts"]["火"],
    "土": rep["bazi"]["wuxing"]["counts"]["土"]})
check("用神结论", bool(rep["bazi"]["yongshen"]))

# 成人改名: 现名诊断 + 改名对比(成人档 ming_report_pro)
# 仅购宝宝档(ming_report) → 成人档 403, 需升级专属档
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴", "style_chips": ["诗意"]}, headers=h)
check("仅购宝宝档→成人档 403(需升级)", r.status_code == 403)
stub.purchases[UID] = {"ming_report_pro"}
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴", "style_chips": ["诗意"]}, headers=h)
check("已购 ming_report_pro → 成人报告 200", r.status_code == 200)
rc = r.json()["report"]["rename_compare"]
check("改名对比含现名诊断", "current" in rc and "issues" in rc["current"])
check("改名对比含推荐名", rc["recommended"]["full"] == "林云舒")
check("改名提示证件变更", "notice" in rc)

# ── T4 跟进 2: 成人改名两档定价(ming_report 19.9 / ming_report_pro 29.9) ──
from src.api.pay import PRODUCTS as PAY_PRODUCTS
check("两档商品注册(19.9/29.9)", PAY_PRODUCTS["ming_report"]["amount"] == 19.9
      and PAY_PRODUCTS["ming_report_pro"]["amount"] == 29.9
      and PAY_PRODUCTS["ming_report"]["type"] == "single"
      and PAY_PRODUCTS["ming_report_pro"]["type"] == "single")

# 未购成人档 → 403
set_member(_MemberStub())
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴"}, headers=h)
check("成人档未购 403", r.status_code == 403)

# 仅购宝宝档: 宝宝报告 200(无 rename_compare), 成人报告 403
stub = _MemberStub()
stub.purchases[UID] = {"ming_report"}
set_member(stub)
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女"}, headers=h)
check("宝宝档已购→宝宝报告 200", r.status_code == 200)
check("宝宝报告无 rename_compare", "rename_compare" not in r.json()["report"])
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴"}, headers=h)
check("宝宝档不覆盖成人档→403", r.status_code == 403)

# 已购 pro: 成人报告 200(含改名对比), 宝宝报告也放行(高档覆盖低档)
stub.purchases[UID] = {"ming_report_pro"}
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴"}, headers=h)
check("已购 pro→成人报告 200", r.status_code == 200)
check("成人报告含 rename_compare", "rename_compare" in r.json()["report"])
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女"}, headers=h)
check("pro 覆盖宝宝档→宝宝报告 200", r.status_code == 200)

# 会员 → 成人档免费
stub3 = _MemberStub()
stub3.members[UID] = "pro"
set_member(stub3)
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴"}, headers=h)
check("会员成人档免费 → 200", r.status_code == 200)

# 体验模式 → 成人档免费
set_member(_MemberStub())
os.environ["EXPERIENCE_MODE"] = "1"
r = client.post("/api/ming/report", json={
    "surname": "林", "given": "云舒", "gender": "女", "mode": "adult",
    "current_name": "晚晴"}, headers=h)
check("体验模式成人档免费 → 200", r.status_code == 200)
os.environ["EXPERIENCE_MODE"] = ""

# 已购 deep_report(同通道商品) → 放行
stub2 = _MemberStub()
stub2.purchases[UID2] = {"deep_report"}
set_member(stub2)
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女"}, headers=h2)
check("已购 deep_report → 200", r.status_code == 200)

# 会员(plan != free) → 免费
stub3 = _MemberStub()
stub3.members[UID2] = "pro"
set_member(stub3)
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女"}, headers=h2)
check("会员深度报告免费 → 200", r.status_code == 200)

# 体验模式 → 全免费(无需购/会员)
set_member(_MemberStub())
os.environ["EXPERIENCE_MODE"] = "1"
r = client.post("/api/ming/report", json={"surname": "林", "given": "云舒", "gender": "女"}, headers=h)
check("体验模式全免费 → 200", r.status_code == 200)
os.environ["EXPERIENCE_MODE"] = ""

# ── 名笺收藏(幂等) ──
r = client.post("/api/ming/save", json={"surname": "林", "given": "云舒", "gender": "女",
                                        "score": 92, "style_note": "诗意"}, headers=h)
check("收藏名笺", r.status_code == 200 and r.json()["saved"] is True)
r = client.post("/api/ming/save", json={"surname": "林", "given": "云舒", "gender": "女",
                                        "score": 92}, headers=h)
check("重复收藏 already 提示", r.json()["already"] is True)
r = client.get("/api/ming/saved", headers=h)
check("收藏列表含云舒", any(it["given"] == "云舒" for it in r.json()["items"]))
r = client.get("/api/ming/saved", headers=h2)
check("收藏列表用户隔离", all(it["given"] != "云舒" for it in r.json()["items"]))

# ── 免费日额度(3 次/日) ──
ming_mod.FREE_DAILY_LIMIT = 3
set_llm(_BadLLM())  # 规则生成即可, 不依赖 LLM
for i in range(3):
    r = client.post("/api/ming/generate", json={"surname": "陈", "gender": "男",
                                                "style_chips": ["大气"]}, headers=h3)
    check(f"额度内第 {i + 1} 次 200", r.status_code == 200)
r = client.post("/api/ming/generate", json={"surname": "陈", "gender": "男"}, headers=h3)
check("超额度 429", r.status_code == 429)
os.environ["EXPERIENCE_MODE"] = "1"
r = client.post("/api/ming/generate", json={"surname": "陈", "gender": "男"}, headers=h3)
check("体验模式跳过额度", r.status_code == 200)
os.environ["EXPERIENCE_MODE"] = ""
ming_mod.FREE_DAILY_LIMIT = 100

# ── 参数校验 ──
r = client.post("/api/ming/generate", json={"surname": "ab", "gender": "女"}, headers=h)
check("非法姓氏 400", r.status_code == 400)
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "女",
                                            "birthYear": 2026, "birthMonth": 5}, headers=h)
check("生辰不完整 400", r.status_code == 400)
r = client.post("/api/ming/generate", json={"surname": "林", "gender": "女",
                                            "mode": "adult", "current_name": ""}, headers=h)
check("成人改名缺现名 400", r.status_code == 400)
r = client.post("/api/ming/report", json={"surname": "林", "given": "ab"}, headers=h)
check("报告非法名字 400", r.status_code == 400)

print(f"\n全部通过: {ok} 项")
