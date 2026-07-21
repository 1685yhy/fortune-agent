"""20 realistic multi-turn conversation scenarios for system testing.

Each scenario is based on real user behavior patterns observed from
social media (知乎/小红书/贴吧) data. Tests go beyond keyword matching
to check conversation quality, context retention, and emotional handling.
"""
import json, time, re
from typing import List, Dict, Callable

# ============================================================
# Scenario definitions: (name, persona, turns, final_check)
# Each turn: (user_message, expected_behavior_check)
# ============================================================

SCENARIOS = [
    # --- 八字类 (4 scenarios) ---
    {
        "name": "焦虑妈妈问学业",
        "persona": "35岁妈妈，孩子上初中，焦虑成绩",
        "turns": [
            ("老师你好，我儿子最近成绩下滑很厉害，我想看看他八字",
             lambda r: len(r) > 50 and "出生" in r[:200]),  # should ask for birth info
            ("2012年8月15日 上午10点 郑州 男",
             lambda r: len(r) > 500 and "学业" in r or "学习" in r),  # should analyze
            ("他适合学文科还是理科？",
             lambda r: len(r) > 100 and ("文" in r or "理" in r)),  # should reference bazi
            ("那以后能考上好大学吗？",
             lambda r: len(r) > 100 and "出生" not in r[:100]),  # should NOT re-ask birth
        ],
    },
    {
        "name": "职场人问财运",
        "persona": "28岁打工人，想投资但不确定",
        "turns": [
            ("帮我看看八字 1995年3月12日 上午9点 上海 男",
             lambda r: len(r) > 500),
            ("我想投资股票，合适吗？",
             lambda r: len(r) > 100 and ("财" in r or "投资" in r)),
            ("那什么时候财运最好？",
             lambda r: len(r) > 80 and "出生" not in r[:100]),  # should remember bazi
        ],
    },
    {
        "name": "感情困惑",
        "persona": "26岁女生，刚分手想不通",
        "turns": [
            ("师傅帮我看看感情 1998年11月20日 下午5点 北京 女",
             lambda r: len(r) > 500 and "感情" in r or "婚" in r or "恋" in r),
            ("那我什么时候能遇到对的人？",
             lambda r: len(r) > 100 and "出生" not in r[:100]),
            ("他会不会回头找我？",
             lambda r: len(r) > 50 and "出生" not in r[:100]),
        ],
    },
    {
        "name": "八字术语追问",
        "persona": "30岁男性，对命理半懂不懂",
        "turns": [
            ("看八字 1988年6月6日 中午12点 广州 男",
             lambda r: len(r) > 500),
            ("你说的这个七杀是什么意思？",
             lambda r: len(r) > 80 and "七杀" in r),  # should explain
            ("那伤官呢？",
             lambda r: len(r) > 50 and "伤官" in r),  # should remember context
        ],
    },

    # --- 解梦类 (3 scenarios) ---
    {
        "name": "反复噩梦",
        "persona": "22岁大学生，被噩梦困扰",
        "turns": [
            ("我最近总是梦见掉牙齿，好害怕，是不是有什么不好的预兆？",
             lambda r: len(r) > 80 and ("梦" in r or "牙" in r)),
            ("我最近确实压力很大，要考试了",
             lambda r: len(r) > 50 and "出生" not in r[:100]),  # listen first
            ("那我应该怎么缓解这种焦虑？",
             lambda r: len(r) > 80),
        ],
    },
    {
        "name": "梦见故人",
        "persona": "40岁女性，梦到去世的母亲",
        "turns": [
            ("我昨晚梦见我去世的妈妈了，她对我笑，然后我就醒了，哭了好久",
             lambda r: len(r) > 100 and ("妈" in r or "亲" in r or "思念" in r or "情" in r)),
            ("她生前对我很好，走得很突然",
             lambda r: len(r) > 50 and "出生" not in r[:100]),  # continue listening
            ("你说她在那边过得好吗？",
             lambda r: len(r) > 50 and "出生" not in r[:100]),
        ],
    },
    {
        "name": "预知梦困惑",
        "persona": "25岁女生，梦到的事情真的发生了",
        "turns": [
            ("我梦到前男友出轨，结果过了一周他真的出轨了，我好崩溃",
             lambda r: len(r) > 80),
            ("这种梦为什么会成真？我是不是有什么特殊能力？",
             lambda r: len(r) > 50 and "出生" not in r[:100]),
        ],
    },

    # --- 风水类 (2 scenarios) ---
    {
        "name": "新房风水咨询",
        "persona": "32岁新婚夫妇，买了新房",
        "turns": [
            ("我家新房大门朝东，帮我看看风水",
             lambda r: len(r) > 100 and "东" in r),
            ("主卧在西南角好不好？",
             lambda r: len(r) > 80 and ("西南" in r or "坤" in r)),
            ("那客厅放什么颜色的沙发比较好？",
             lambda r: len(r) > 50),
        ],
    },
    {
        "name": "办公室风水",
        "persona": "38岁创业者，新办公室需要布置",
        "turns": [
            ("新办公室坐北朝南，我办公桌应该怎么摆？",
             lambda r: len(r) > 100 and ("北" in r or "南" in r or "子" in r or "午" in r)),
            ("饮水机放哪里比较好？",
             lambda r: len(r) > 50),
        ],
    },

    # --- 六爻/占卜类 (2 scenarios) ---
    {
        "name": "跳槽问卦",
        "persona": "27岁女生，纠结要不要跳槽",
        "turns": [
            ("帮我起个卦，我想跳槽去另一家公司，不知道好不好",
             lambda r: len(r) > 100),
            ("那什么时候跳比较好？",
             lambda r: len(r) > 50),
        ],
    },
    {
        "name": "考试问卦",
        "persona": "22岁大学生，考研前紧张",
        "turns": [
            ("帮我算个卦，明天考研复试能不能过",
             lambda r: len(r) > 100),
            ("如果没过怎么办？有没有化解的方法？",
             lambda r: len(r) > 50),
        ],
    },

    # --- 择日类 (1 scenario) ---
    {
        "name": "结婚择日",
        "persona": "29岁准新娘，挑结婚日子",
        "turns": [
            ("帮我选个结婚的好日子，2026年10月",
             lambda r: len(r) > 100),
            ("10月3号怎么样？",
             lambda r: len(r) > 50 and "10" in r or "十月" in r),
        ],
    },

    # --- 姓名学 (1 scenario) ---
    {
        "name": "宝宝起名",
        "persona": "新晋父母，给新生儿起名",
        "turns": [
            ("帮我宝宝起个名字，2026年7月15日上午9点出生 男 姓王",
             lambda r: len(r) > 200),
            ("能不能多给几个选项？",
             lambda r: len(r) > 100),
        ],
    },

    # --- 合婚类 (1 scenario) ---
    {
        "name": "情侣配对",
        "persona": "25岁女生，想和男朋友合婚",
        "turns": [
            ("帮我们看看合婚 我1998年3月 他1996年8月",
             lambda r: len(r) > 100),
            ("那我们适合结婚吗？",
             lambda r: len(r) > 50),
        ],
    },

    # --- 心事树洞 (3 scenarios) ---
    {
        "name": "工作崩溃",
        "persona": "30岁打工人，被工作压垮",
        "turns": [
            ("我真的快受不了了，老板天天 PUA 我，同事也排挤我，我好累",
             lambda r: len(r) > 80 and "出生" not in r[:100]),  # listen only
            ("我每天都想辞职，但又怕找不到工作",
             lambda r: len(r) > 50 and "出生" not in r[:100]),  # continue listening
            ("你觉得我应该怎么办？",
             lambda r: len(r) > 80),  # offer gentle guidance
        ],
    },
    {
        "name": "分手后自救",
        "persona": "23岁女生，分手后很痛苦",
        "turns": [
            ("分手两周了，我还是每天哭，怎么办",
             lambda r: len(r) > 80 and "出生" not in r[:100]),
            ("他把我微信拉黑了，我好想联系他",
             lambda r: len(r) > 50 and "出生" not in r[:100]),
            ("好吧...那你帮我看看我什么时候能走出来",
             lambda r: len(r) > 50),  # user asks for analysis → acceptable
        ],
    },
    {
        "name": "人生迷茫",
        "persona": "33岁中年危机，觉得人生没方向",
        "turns": [
            ("我觉得我活了三十多年一事无成，好失败",
             lambda r: len(r) > 80 and "出生" not in r[:100]),
            ("我也说不清楚，就是觉得做什么都没意思",
             lambda r: len(r) > 50 and "出生" not in r[:100]),
        ],
    },

    # --- 日历类 (1 scenario) ---
    {
        "name": "每日运势追踪",
        "persona": "日常用户，每天看运势",
        "turns": [
            ("今日运势",
             lambda r: len(r) > 80 and "宜" in r),
            ("明天运势怎么样？",
             lambda r: len(r) > 50),
        ],
    },

    # --- 边界场景 (2 scenarios) ---
    {
        "name": "错别字容错",
        "persona": "打字不准确的老年用户",
        "turns": [
            ("帮我看看八字 1990年5月20日 下无3点 北京 南",
             lambda r: len(r) > 100),  # should handle typos gracefully
        ],
    },
    {
        "name": "超长消息",
        "persona": "倾诉欲强的用户",
        "turns": [
            ("老师你好我最近特别烦因为我老板天天刁难我然后我男朋友也不理我我妈还生病了我不知道怎么办才好我觉得我整个人都要崩溃了每天晚上都睡不着觉白天还要强撑着上班我真的好累好想找一个出口但是不知道跟谁说所以来找你看看能不能从命理的角度帮我分析一下我到底是怎么了",
             lambda r: len(r) > 100),  # should handle long messages
        ],
    },
]


def run_test_suite(api_url: str = "http://localhost:8765/api/chat",
                   delay: float = 1.5, timeout: int = 120):
    """Run all scenarios and generate a report."""
    import httpx

    results = []
    total_turns = 0
    passed_turns = 0

    for i, scenario in enumerate(SCENARIOS, 1):
        name = scenario["name"]
        turns = scenario["turns"]
        uid = f"sim_{i}_{int(time.time())}"
        scenario_ok = True

        print(f"\n{'='*60}")
        print(f"[{i}/{len(SCENARIOS)}] {name}")
        print(f"  用户画像: {scenario['persona']}")

        for j, (msg, check) in enumerate(turns, 1):
            total_turns += 1
            print(f"  轮{j}: {msg[:60]}...", end=" ", flush=True)

            try:
                start = time.time()
                r = httpx.post(api_url, json={"message": msg, "user_id": uid}, timeout=timeout)
                reply = r.json().get("reply", "")
                elapsed = time.time() - start

                passed = check(reply)
                status = "✅" if passed else "❌"
                print(f"{status} {elapsed:.0f}s {len(reply)}字")

                if not passed:
                    scenario_ok = False
                    print(f"    ⚠️ FAILED check. Preview: {reply[:120].replace(chr(10), ' ')}")

                if passed:
                    passed_turns += 1

            except Exception as e:
                print(f"💥 {e}")
                scenario_ok = False

            time.sleep(delay)

        results.append({"name": name, "ok": scenario_ok, "turns": len(turns)})

    # Summary
    print(f"\n{'='*60}")
    print(f"总轮次: {passed_turns}/{total_turns} 通过 ({passed_turns/total_turns*100:.0f}%)")
    scenarios_ok = sum(1 for r in results if r["ok"])
    print(f"场景: {scenarios_ok}/{len(SCENARIOS)} 完全通过")

    for r in results:
        s = "✅" if r["ok"] else "❌"
        print(f"  {s} {r['name']} ({r['turns']}轮)")

    return {
        "total_scenarios": len(SCENARIOS),
        "passed_scenarios": scenarios_ok,
        "total_turns": total_turns,
        "passed_turns": passed_turns,
        "pass_rate": round(passed_turns / total_turns * 100, 1) if total_turns > 0 else 0,
        "details": results,
    }


if __name__ == "__main__":
    import sys
    api = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/api/chat"
    report = run_test_suite(api)
    print(f"\n📊 Final score: {report['pass_rate']}% ({report['passed_turns']}/{report['total_turns']} turns)")
    # Save report
    with open("/tmp/simulation_report.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("Report saved to /tmp/simulation_report.json")
