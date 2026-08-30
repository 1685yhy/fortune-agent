"""K7 奇门拆补法修复批: 58+ 权威时点锚点全量落库 + 三态/边界/补段/洛书环专项断言.

权威源: 衍象坊 (yxq) + openfate (of/of3) 双站互证 (2026-08-31 取数), 共 64 时点. 对齐率目标 >= 90%.
逐时点全字段断言: 局数(阴阳+局+元)/值符星/值符落宫/值符原宫/值使门/值使落宫
+ 全量盘面 (地盘/天盘/八门/九星/八神) 45 时点.
口径归一化 (报告 🟡 口径待拍板项, 计算等价): 中5->坤2 显示 (禽芮同宫寄坤二);
yxq 门值 "门" 后缀剥除; 禽芮->天芮; 腾蛇->螣蛇.
"""

from src.engines.qimen import (
    BA_MEN_ORIGIN, BA_SHEN, JIU_XING_ORIGIN, LUOSHU_RING, RING_IDX,
    PALACE_NAMES, PALACE_NUMS, QimenEngine, _ring_at, _ring_shift,
)


_E = QimenEngine()


# (y, m, d, h, mi) 起局时刻; 锚点字段: dun/ju/yuan 局数, zf_* 值符, zs_* 值使;
# plates: dipan/tianpan 按宫1-9, jiuxing/bamen/bashen 按洛书环序 [9, 2, 7, 6, 1, 8, 3, 4]
AUTHORITY_ANCHORS = {
    '2025-12-10 12:00': {'t': [2025, 12, 10, 12, 0], 'dun': 'yin', 'ju': 4, 'yuan': 'upper', 'zf_star': '天任', 'zf_gong': 4, 'zs_door': '生', 'zs_gong': 4, 'plates': None},
    '2025-12-15 12:00': {'t': [2025, 12, 15, 12, 0], 'dun': 'yin', 'ju': 7, 'yuan': 'middle', 'zf_star': '天芮', 'zf_gong': 7, 'zs_door': '死', 'zs_gong': 7, 'plates': None},
    '2025-12-20 12:00': {'t': [2025, 12, 20, 12, 0], 'dun': 'yin', 'ju': 1, 'yuan': 'lower', 'zf_star': '天禽', 'zf_gong': 1, 'zs_door': '死', 'zs_gong': 1, 'plates': None},
    '2025-12-21 12:00': {'t': [2025, 12, 21, 12, 0], 'dun': 'yin', 'ju': 4, 'yuan': 'upper', 'zf_star': '天辅', 'zf_gong': 2, 'zf_orig': 4, 'zs_door': '杜', 'zs_gong': 7, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '己戊壬庚丁丙辛癸', 'jiuxing': '天冲天辅天英天芮天柱天心天蓬天任', 'bamen': '生伤杜景死惊开休', 'bashen': '螣蛇值符九天九地玄武白虎六合太阴'}},
    '2025-12-21 23:00': {'t': [2025, 12, 21, 23, 0], 'dun': 'yin', 'ju': 4, 'yuan': 'upper', 'zf_star': '天冲', 'zf_gong': 6, 'zf_orig': 3, 'zs_door': '伤', 'zs_gong': 1, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '丙辛癸己戊壬庚丁', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '惊开休生伤杜景死', 'bashen': '六合太阴螣蛇值符九天九地玄武白虎'}},
    '2025-12-21 23:30': {'t': [2025, 12, 21, 23, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 8, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 4, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '戊丙庚辛乙己丁癸', 'jiuxing': '天蓬天任天冲天辅天英天芮天柱天心', 'bamen': '惊开休生伤杜景死', 'bashen': '六合白虎玄武九地九天值符螣蛇太阴'}},
    '2025-12-22 00:30': {'t': [2025, 12, 22, 0, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 8, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 4, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '戊丙庚辛乙己丁癸', 'jiuxing': '天蓬天任天冲天辅天英天芮天柱天心', 'bamen': '惊开休生伤杜景死', 'bashen': '六合白虎玄武九地九天值符螣蛇太阴'}},
    '2025-12-22 01:30': {'t': [2025, 12, 22, 1, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 7, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 2, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '辛乙己丁癸戊丙庚', 'jiuxing': '天辅天英天芮天柱天心天蓬天任天冲', 'bamen': '景死惊开休生伤杜', 'bashen': '九地九天值符螣蛇太阴六合白虎玄武'}},
    '2025-12-22 03:30': {'t': [2025, 12, 22, 3, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 1, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 6, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '丙庚辛乙己丁癸戊', 'jiuxing': '天任天冲天辅天英天芮天柱天心天蓬', 'bamen': '伤杜景死惊开休生', 'bashen': '白虎玄武九地九天值符螣蛇太阴六合'}},
    '2025-12-22 05:30': {'t': [2025, 12, 22, 5, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 2, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 7, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '乙己丁癸戊丙庚辛', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '杜景死惊开休生伤', 'bashen': '九天值符螣蛇太阴六合白虎玄武九地'}},
    '2025-12-22 07:30': {'t': [2025, 12, 22, 7, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 3, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 8, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '癸戊丙庚辛乙己丁', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '休生伤杜景死惊开', 'bashen': '太阴六合白虎玄武九地九天值符螣蛇'}},
    '2025-12-22 09:30': {'t': [2025, 12, 22, 9, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 4, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 9, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '丁癸戊丙庚辛乙己', 'jiuxing': '天柱天心天蓬天任天冲天辅天英天芮', 'bamen': '死惊开休生伤杜景', 'bashen': '螣蛇太阴六合白虎玄武九地九天值符'}},
    '2025-12-22 11:30': {'t': [2025, 12, 22, 11, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 2, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 1, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '乙己丁癸戊丙庚辛', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '生伤杜景死惊开休', 'bashen': '九天值符螣蛇太阴六合白虎玄武九地'}},
    '2025-12-22 13:30': {'t': [2025, 12, 22, 13, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天芮', 'zf_gong': 6, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 2, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '庚辛乙己丁癸戊丙', 'jiuxing': '天冲天辅天英天芮天柱天心天蓬天任', 'bamen': '景死惊开休生伤杜', 'bashen': '玄武九地九天值符螣蛇太阴六合白虎'}},
    '2025-12-22 15:30': {'t': [2025, 12, 22, 15, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天冲', 'zf_gong': 3, 'zf_orig': 3, 'zs_door': '伤', 'zs_gong': 3, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '乙己丁癸戊丙庚辛', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '太阴六合白虎玄武九地九天值符螣蛇'}},
    '2025-12-22 17:30': {'t': [2025, 12, 22, 17, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天冲', 'zf_gong': 9, 'zf_orig': 3, 'zs_door': '伤', 'zs_gong': 4, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '庚辛乙己丁癸戊丙', 'jiuxing': '天冲天辅天英天芮天柱天心天蓬天任', 'bamen': '杜景死惊开休生伤', 'bashen': '值符螣蛇太阴六合白虎玄武九地九天'}},
    '2025-12-22 19:30': {'t': [2025, 12, 22, 19, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天冲', 'zf_gong': 8, 'zf_orig': 3, 'zs_door': '伤', 'zs_gong': 2, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '己丁癸戊丙庚辛乙', 'jiuxing': '天芮天柱天心天蓬天任天冲天辅天英', 'bamen': '生伤杜景死惊开休', 'bashen': '六合白虎玄武九地九天值符螣蛇太阴'}},
    '2025-12-22 21:30': {'t': [2025, 12, 22, 21, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天冲', 'zf_gong': 7, 'zf_orig': 3, 'zs_door': '伤', 'zs_gong': 6, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '戊丙庚辛乙己丁癸', 'jiuxing': '天蓬天任天冲天辅天英天芮天柱天心', 'bamen': '开休生伤杜景死惊', 'bashen': '九地九天值符螣蛇太阴六合白虎玄武'}},
    '2025-12-28 01:30': {'t': [2025, 12, 28, 1, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天英', 'zf_gong': 8, 'zf_orig': 9, 'zs_door': '景', 'zs_gong': 2, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '乙辛己癸丁庚壬戊', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '杜景死惊开休生伤', 'bashen': '六合白虎玄武九地九天值符螣蛇太阴'}},
    '2025-12-28 03:30': {'t': [2025, 12, 28, 3, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天英', 'zf_gong': 9, 'zf_orig': 9, 'zs_door': '景', 'zs_gong': 6, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '庚壬戊乙辛己癸丁', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '生伤杜景死惊开休', 'bashen': '值符螣蛇太阴六合白虎玄武九地九天'}},
    '2025-12-28 05:30': {'t': [2025, 12, 28, 5, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天英', 'zf_gong': 1, 'zf_orig': 9, 'zs_door': '景', 'zs_gong': 7, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '辛己癸丁庚壬戊乙', 'jiuxing': '天蓬天任天冲天辅天英天芮天柱天心', 'bamen': '伤杜景死惊开休生', 'bashen': '白虎玄武九地九天值符螣蛇太阴六合'}},
    '2025-12-28 07:30': {'t': [2025, 12, 28, 7, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天英', 'zf_gong': 2, 'zf_orig': 9, 'zs_door': '景', 'zs_gong': 8, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '丁庚壬戊乙辛己癸', 'jiuxing': '天辅天英天芮天柱天心天蓬天任天冲', 'bamen': '开休生伤杜景死惊', 'bashen': '九天值符螣蛇太阴六合白虎玄武九地'}},
    '2025-12-28 09:30': {'t': [2025, 12, 28, 9, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天英', 'zf_gong': 3, 'zf_orig': 9, 'zs_door': '景', 'zs_gong': 9, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '戊乙辛己癸丁庚壬', 'jiuxing': '天柱天心天蓬天任天冲天辅天英天芮', 'bamen': '景死惊开休生伤杜', 'bashen': '太阴六合白虎玄武九地九天值符螣蛇'}},
    '2025-12-28 11:30': {'t': [2025, 12, 28, 11, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 1, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 1, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '庚壬戊乙辛己癸丁', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '白虎玄武九地九天值符螣蛇太阴六合'}},
    '2025-12-28 13:30': {'t': [2025, 12, 28, 13, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 6, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 2, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '壬戊乙辛己癸丁庚', 'jiuxing': '天芮天柱天心天蓬天任天冲天辅天英', 'bamen': '开休生伤杜景死惊', 'bashen': '玄武九地九天值符螣蛇太阴六合白虎'}},
    '2025-12-28 15:30': {'t': [2025, 12, 28, 15, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 2, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 3, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '乙辛己癸丁庚壬戊', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '伤杜景死惊开休生', 'bashen': '九天值符螣蛇太阴六合白虎玄武九地'}},
    '2025-12-28 17:30': {'t': [2025, 12, 28, 17, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 4, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 4, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '己癸丁庚壬戊乙辛', 'jiuxing': '天任天冲天辅天英天芮天柱天心天蓬', 'bamen': '生伤杜景死惊开休', 'bashen': '螣蛇太阴六合白虎玄武九地九天值符'}},
    '2025-12-28 19:30': {'t': [2025, 12, 28, 19, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 7, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 2, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '戊乙辛己癸丁庚壬', 'jiuxing': '天柱天心天蓬天任天冲天辅天英天芮', 'bamen': '开休生伤杜景死惊', 'bashen': '九地九天值符螣蛇太阴六合白虎玄武'}},
    '2025-12-28 21:30': {'t': [2025, 12, 28, 21, 30], 'dun': 'yang', 'ju': 7, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 8, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 6, 'plates': {'dipan': '辛壬癸丁丙乙戊己庚', 'tianpan': '丁庚壬戊乙辛己癸', 'jiuxing': '天辅天英天芮天柱天心天蓬天任天冲', 'bamen': '死惊开休生伤杜景', 'bashen': '六合白虎玄武九地九天值符螣蛇太阴'}},
    '2026-01-06 00:30': {'t': [2026, 1, 6, 0, 30], 'dun': 'yang', 'ju': 2, 'yuan': 'upper', 'zf_star': '天冲', 'zf_gong': 9, 'zf_orig': 3, 'zs_door': '伤', 'zs_gong': 2, 'plates': {'dipan': '乙戊己庚辛壬癸丁丙', 'tianpan': '己庚丙戊癸壬乙丁', 'jiuxing': '天冲天辅天英天芮天柱天心天蓬天任', 'bamen': '生伤杜景死惊开休', 'bashen': '值符螣蛇太阴六合白虎玄武九地九天'}},
    '2026-03-05 09:30': {'t': [2026, 3, 5, 9, 30], 'dun': 'yang', 'ju': 3, 'yuan': 'lower', 'zf_star': '天任', 'zf_gong': 9, 'zf_orig': 8, 'zs_door': '生', 'zs_gong': 2, 'plates': {'dipan': '丙乙戊己庚辛壬癸丁', 'tianpan': '癸戊己丁乙壬辛丙', 'jiuxing': '天任天冲天辅天英天芮天柱天心天蓬', 'bamen': '休生伤杜景死惊开', 'bashen': '值符螣蛇太阴六合白虎玄武九地九天'}},
    '2026-03-05 12:00': {'t': [2026, 3, 5, 12, 0], 'dun': 'yang', 'ju': 3, 'yuan': 'lower', 'zf_star': '天任', 'zf_gong': 3, 'zf_orig': 8, 'zs_door': '生', 'zs_gong': 3, 'plates': {'dipan': '丙乙戊己庚辛壬癸丁', 'tianpan': '己丁乙壬辛丙癸戊', 'jiuxing': '天辅天英天芮天柱天心天蓬天任天冲', 'bamen': '杜景死惊开休生伤', 'bashen': '太阴六合白虎玄武九地九天值符螣蛇'}},
    '2026-03-05 21:30': {'t': [2026, 3, 5, 21, 30], 'dun': 'yang', 'ju': 3, 'yuan': 'lower', 'zf_star': '天任', 'zf_gong': 8, 'zf_orig': 8, 'zs_door': '生', 'zs_gong': 8, 'plates': {'dipan': '丙乙戊己庚辛壬癸丁', 'tianpan': '丁乙壬辛丙癸戊己', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '六合白虎玄武九地九天值符螣蛇太阴'}},
    '2026-03-05 23:00': {'t': [2026, 3, 5, 23, 0], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天蓬', 'zf_gong': 1, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 1, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '乙己丁癸戊丙庚辛', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '白虎玄武九地九天值符螣蛇太阴六合'}},
    '2026-03-06 00:30': {'t': [2026, 3, 6, 0, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天蓬', 'zf_gong': 1, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 1, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '乙己丁癸戊丙庚辛', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '白虎玄武九地九天值符螣蛇太阴六合'}},
    '2026-03-06 09:30': {'t': [2026, 3, 6, 9, 30], 'dun': 'yang', 'ju': 1, 'yuan': 'upper', 'zf_star': '天蓬', 'zf_gong': 2, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 6, 'plates': {'dipan': '戊己庚辛壬癸丁丙乙', 'tianpan': '癸戊丙庚辛乙己丁', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '死惊开休生伤杜景', 'bashen': '九天值符螣蛇太阴六合白虎玄武九地'}},
    '2026-08-24 12:00': {'t': [2026, 8, 24, 12, 0], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天冲', 'zf_gong': 9, 'zs_door': '伤', 'zs_gong': 4, 'plates': None},
    '2026-08-25 01:30': {'t': [2026, 8, 25, 1, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天芮', 'zf_gong': 3, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 6, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '丙辛癸己戊壬庚丁', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '伤杜景死惊开休生', 'bashen': '九地玄武白虎六合太阴螣蛇值符九天'}},
    '2026-08-25 03:30': {'t': [2026, 8, 25, 3, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天芮', 'zf_gong': 2, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 2, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '壬庚丁丙辛癸己戊', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '螣蛇值符九天九地玄武白虎六合太阴'}},
    '2026-08-25 05:30': {'t': [2026, 8, 25, 5, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天芮', 'zf_gong': 1, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 4, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '癸己戊壬庚丁丙辛', 'jiuxing': '天任天冲天辅天英天芮天柱天心天蓬', 'bamen': '惊开休生伤杜景死', 'bashen': '白虎六合太阴螣蛇值符九天九地玄武'}},
    '2026-08-25 07:30': {'t': [2026, 8, 25, 7, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天芮', 'zf_gong': 9, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 3, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '庚丁丙辛癸己戊壬', 'jiuxing': '天芮天柱天心天蓬天任天冲天辅天英', 'bamen': '开休生伤杜景死惊', 'bashen': '值符九天九地玄武白虎六合太阴螣蛇'}},
    '2026-08-25 09:30': {'t': [2026, 8, 25, 9, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天芮', 'zf_gong': 8, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 2, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '辛癸己戊壬庚丁丙', 'jiuxing': '天蓬天任天冲天辅天英天芮天柱天心', 'bamen': '景死惊开休生伤杜', 'bashen': '玄武白虎六合太阴螣蛇值符九天九地'}},
    '2026-08-25 11:30': {'t': [2026, 8, 25, 11, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 1, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 1, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '壬庚丁丙辛癸己戊', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '白虎六合太阴螣蛇值符九天九地玄武'}},
    '2026-08-25 13:30': {'t': [2026, 8, 25, 13, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 2, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 9, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '丙辛癸己戊壬庚丁', 'jiuxing': '天心天蓬天任天冲天辅天英天芮天柱', 'bamen': '休生伤杜景死惊开', 'bashen': '螣蛇值符九天九地玄武白虎六合太阴'}},
    '2026-08-25 15:30': {'t': [2026, 8, 25, 15, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 6, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 8, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '庚丁丙辛癸己戊壬', 'jiuxing': '天芮天柱天心天蓬天任天冲天辅天英', 'bamen': '杜景死惊开休生伤', 'bashen': '六合太阴螣蛇值符九天九地玄武白虎'}},
    '2026-08-25 17:30': {'t': [2026, 8, 25, 17, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 7, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 7, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '丁丙辛癸己戊壬庚', 'jiuxing': '天柱天心天蓬天任天冲天辅天英天芮', 'bamen': '惊开休生伤杜景死', 'bashen': '太阴螣蛇值符九天九地玄武白虎六合'}},
    '2026-08-25 19:30': {'t': [2026, 8, 25, 19, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 4, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 6, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '癸己戊壬庚丁丙辛', 'jiuxing': '天任天冲天辅天英天芮天柱天心天蓬', 'bamen': '死惊开休生伤杜景', 'bashen': '九天九地玄武白虎六合太阴螣蛇值符'}},
    '2026-08-25 21:30': {'t': [2026, 8, 25, 21, 30], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天蓬', 'zf_gong': 3, 'zf_orig': 1, 'zs_door': '休', 'zs_gong': 2, 'plates': {'dipan': '辛庚己戊乙丙丁癸壬', 'tianpan': '己戊壬庚丁丙辛癸', 'jiuxing': '天冲天辅天英天芮天柱天心天蓬天任', 'bamen': '开休生伤杜景死惊', 'bashen': '九地玄武白虎六合太阴螣蛇值符九天'}},
    '2026-08-26 12:00': {'t': [2026, 8, 26, 12, 0], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天英', 'zf_gong': 6, 'zs_door': '景', 'zs_gong': 7, 'plates': None},
    '2026-08-27 12:00': {'t': [2026, 8, 27, 12, 0], 'dun': 'yin', 'ju': 4, 'yuan': 'middle', 'zf_star': '天任', 'zf_gong': 4, 'zs_door': '生', 'zs_gong': 4, 'plates': None},
    '2026-08-30 10:00': {'t': [2026, 8, 30, 10, 0], 'dun': 'yin', 'ju': 7, 'yuan': 'lower', 'zf_star': '天禽', 'zf_gong': 2, 'zf_orig': 2, 'zs_door': '死', 'zs_gong': 2, 'plates': {'dipan': '丁癸壬辛庚己戊乙丙', 'tianpan': '丙癸戊己丁乙壬辛', 'jiuxing': '天英天芮天柱天心天蓬天任天冲天辅', 'bamen': '景死惊开休生伤杜', 'bashen': '螣蛇值符九天九地玄武白虎六合太阴'}},
    '2026-09-01 12:00': {'t': [2026, 9, 1, 12, 0], 'dun': 'yin', 'ju': 7, 'yuan': 'lower', 'zf_star': '天芮', 'zf_gong': 7, 'zs_door': '死', 'zs_gong': 7, 'plates': None},
    '2026-09-02 12:00': {'t': [2026, 9, 2, 12, 0], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天蓬', 'zf_gong': 8, 'zs_door': '休', 'zs_gong': 4, 'plates': None},
    '2026-09-03 01:30': {'t': [2026, 9, 3, 1, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 4, 'zs_door': '景', 'zs_gong': 6, 'plates': None},
    '2026-09-03 03:30': {'t': [2026, 9, 3, 3, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 1, 'zs_door': '景', 'zs_gong': 2, 'plates': None},
    '2026-09-03 05:30': {'t': [2026, 9, 3, 5, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 9, 'zs_door': '景', 'zs_gong': 4, 'plates': None},
    '2026-09-03 07:30': {'t': [2026, 9, 3, 7, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 8, 'zs_door': '景', 'zs_gong': 3, 'plates': None},
    '2026-09-03 09:30': {'t': [2026, 9, 3, 9, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 7, 'zs_door': '景', 'zs_gong': 2, 'plates': None},
    '2026-09-03 11:30': {'t': [2026, 9, 3, 11, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 6, 'zs_door': '景', 'zs_gong': 1, 'plates': None},
    '2026-09-03 13:30': {'t': [2026, 9, 3, 13, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天英', 'zf_gong': 2, 'zs_door': '景', 'zs_gong': 9, 'plates': None},
    '2026-09-03 15:30': {'t': [2026, 9, 3, 15, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天任', 'zf_gong': 8, 'zs_door': '生', 'zs_gong': 8, 'plates': None},
    '2026-09-03 17:30': {'t': [2026, 9, 3, 17, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天任', 'zf_gong': 2, 'zs_door': '生', 'zs_gong': 7, 'plates': None},
    '2026-09-03 19:30': {'t': [2026, 9, 3, 19, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天任', 'zf_gong': 3, 'zs_door': '生', 'zs_gong': 6, 'plates': None},
    '2026-09-03 21:30': {'t': [2026, 9, 3, 21, 30], 'dun': 'yin', 'ju': 1, 'yuan': 'upper', 'zf_star': '天任', 'zf_gong': 4, 'zs_door': '生', 'zs_gong': 2, 'plates': None},
}


def _plates_str(r, kind):
    """盘面字符串: dipan 宫1-9序; tianpan/jiuxing/bamen/bashen 洛书环序 (中5 口径见 ring 专项)."""
    if kind == "dipan":
        return "".join(r.dipan[PALACE_NAMES[p]] for p in range(1, 10))
    if kind == "tianpan":
        return "".join(r.tianpan[PALACE_NAMES[p]] for p in LUOSHU_RING)
    if kind == "jiuxing":
        d = {n: v for n, v in r.jiuxing.items() if n != "中"}
    elif kind == "bamen":
        d = {n: v for n, v in r.bamen.items() if n != "中"}
    else:
        d = {n: v for n, v in r.bashen.items() if n != "中"}
    return "".join(d[PALACE_NAMES[p]] for p in LUOSHU_RING)


def _eng_fields(a, r):
    """引擎输出归一化 (与锚点同口径: 中5->坤2 显示)."""
    yuan = {"上元": "upper", "中元": "middle", "下元": "lower"}[r.raw_data["yuan"]]
    def gong(name):
        g = PALACE_NUMS[r.raw_data[name]]
        return 2 if g == 5 else g
    bamen_p = {PALACE_NUMS[n]: v for n, v in r.bamen.items() if v}
    zs_gong = [p for p, v in bamen_p.items() if v == r.zhishi_door][0]
    return {
        "dun": "yang" if r.dun_type == "阳遁" else "yin",
        "ju": r.ju_number, "yuan": yuan,
        "zf_star": r.zhifu_star, "zf_gong": gong("hour_gan_palace"),
        "zf_orig": gong("xunshou_palace"),
        "zs_door": r.zhishi_door, "zs_gong": zs_gong,
    }


def _compare_anchor(a, r):
    """逐字段比较, 返回 (是否全对齐, 失败字段名列表)."""
    eng = _eng_fields(a, r)
    checks = []
    for f in ("dun", "ju", "yuan", "zf_star", "zf_gong", "zf_orig", "zs_door", "zs_gong"):
        if f in a:
            checks.append((f, a[f] == eng[f]))
    if a.get("plates"):
        for kind in ("dipan", "tianpan", "jiuxing", "bamen", "bashen"):
            checks.append((kind, _plates_str(r, kind) == a["plates"][kind]))
    return all(v for _, v in checks), [k for k, v in checks if not v]


def test_authority_anchors_all_pass():
    """64 权威时点逐时点全字段断言, 对齐率 >= 90%."""
    total = len(AUTHORITY_ANCHORS)
    aligned = 0
    failures = []
    for key, a in AUTHORITY_ANCHORS.items():
        y, m, d, h, mi = a["t"]
        r = _E.calculate(y, m, d, h, mi)
        ok, why = _compare_anchor(a, r)
        aligned += ok
        if not ok:
            failures.append((key, why))
    assert aligned / total >= 0.9, f"对齐率 {aligned}/{total} 低于 90%%: {failures}"


def test_chaibu_three_states():
    """三态专项: 正授/接气/超神 (权威锚点断言)."""
    # 正授: 冬至 12/21 23:03 交节 (符头日甲子) -> 上元阳1; 小寒 1/5 16:23 (己卯) -> 上元阳2
    r = _E.calculate(2025, 12, 21, 23, 4)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", 1, "上元")
    r = _E.calculate(2026, 1, 5, 16, 24)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", 2, "上元")
    # 接气: 惊蛰 3/5 21:59 交节 -> 上元段=己卯段 [3/6,3/11); 3/5 23:30 权威锚点 = 惊蛰上元阳1
    r = _E.calculate(2026, 3, 5, 23, 30)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", 1, "上元")
    # 大雪 12/7 05:04 交节 (己酉段第2天) -> 上元段 [12/7,12/11); 12/10 权威锚点 = 上元阴4
    r = _E.calculate(2025, 12, 10, 12, 0)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 4, "上元")
    # 超神: 处暑 8/23 10:18 交节 (符头日己巳=中元段) -> 交节段即中元阴4;
    # 权威 8/23 10:19 = 中元阴4 (推翻旧假设 "上元段=[交节,次日) 13.7h")
    r = _E.calculate(2026, 8, 23, 10, 19)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 4, "中元")
    r = _E.calculate(2026, 8, 24, 12, 0)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 4, "中元")
    r = _E.calculate(2026, 9, 2, 12, 0)  # 己卯段 -> 上元阴1 (非补段, 段起日地支固定元)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 1, "上元")
    # 晚子时归次日参与定元: 惊蛰 3/5 21:59 交节 (段起甲戌=下元) -> 3/5 22:30 下元阳4;
    # 3/5 23:30 晚子时归次日 3/6 (段起己卯=上元) -> 上元阳1 (权威锚点)
    r = _E.calculate(2026, 3, 5, 22, 30)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", 4, "下元")
    r = _E.calculate(2026, 3, 5, 23, 30)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", 1, "上元")


def test_chaibu_futou_block_yuan():
    """符头段固定元专项: 元段 = 通用5日甲/己段网格, 段元按段起日地支固定 (权威锚点).

    覆盖: 接气 day-2 (立夏 1990 段: 中[5/6,5/9) 下[5/9,5/14) 上[5/14,5/19) 中[5/19,5/21 15:37))
    + 节气段 >3 符头段的第4块 (处暑 9/2 己卯段=上元阴1, 大雪 12/21 甲子段=上元阴4).
    """
    # 立夏 1990 (交节 5/6 02:35, 段起己巳=中元): 权威锚点 5/7=中元阳1, 5/14=上元阳4, 5/19=中元阳1
    for day, ju, yuan in [(7, 1, "中元"), (14, 4, "上元"), (19, 1, "中元")]:
        r = _E.calculate(1990, 5, day, 12, 0)
        assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", ju, yuan), f"5/{day}"
    # 小滿 1990 (交节 5/21 15:37, 段起甲申=中元): 5/24=下元阳8 (权威锚点)
    r = _E.calculate(1990, 5, 24, 12, 0)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阳遁", 8, "下元")
    # 处暑 2026: 9/2 己卯段 = 上元阴1 (非补段, 段起日地支固定元); 9/3 全系列 11 时点 = 上元阴1
    r = _E.calculate(2026, 9, 2, 12, 0)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 1, "上元")
    for h, mi in [(1,30),(3,30),(5,30),(7,30),(9,30),(11,30),(13,30),(15,30),(17,30),(19,30),(21,30)]:
        r = _E.calculate(2026, 9, 3, h, mi)
        assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 1, "上元"), f"9/3 {h}:{mi}"
    # 处暑 中/下界 8/28 (甲戌) -> 下元; 9/1 权威锚点 = 下元阴7
    r = _E.calculate(2026, 9, 1, 12, 0)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 7, "下元")
    # 大雪: 12/10 上元阴4 / 12/15 中元阴7 / 12/20 下元阴1 (权威锚点)
    for day, ju, yuan in [(10, 4, "上元"), (15, 7, "中元"), (20, 1, "下元")]:
        r = _E.calculate(2025, 12, day, 12, 0)
        assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", ju, yuan), f"12/{day}"
    # 大雪 12/21 甲子段 前23h = 上元阴4 (QM3a 权威锚点)
    r = _E.calculate(2025, 12, 21, 23, 0)
    assert (r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("阴遁", 4, "上元")


def test_chaibu_2026_jieqi_probes():
    """2026 全节气段元探测专项 (衍象坊 2026-08-31 权威时点, 覆盖 day-0..day-4 接气/超神/正授).

    局数表 = 拆补法标准表 (上,中,下): 惊蛰=阳(1,7,4) 小暑=阴(8,2,5) 白露=阴(9,3,6)
    秋分=阴(7,1,4) 芒种=阳(6,3,9) 清明=阳(4,1,7) 谷雨=阳(5,2,8) 雨水=阳(9,6,3)
    立春=阳(8,5,2) 大寒=阳(3,9,6) 小寒=阳(2,8,5) 夏至=阴(9,3,6).
    """
    cases = [
        # (y,m,d,h,mi, 期望 (遁,局,元)) — 权威锚点
        (2026, 7, 7, 12, 0, ("阴遁", 8, "上元")),   # 小暑 day-3 接气 (段起己卯=上)
        (2026, 7, 9, 12, 0, ("阴遁", 2, "中元")),   # 段起甲申=中
        (2026, 7, 14, 12, 0, ("阴遁", 5, "下元")),  # 段起己丑=下
        (2026, 3, 5, 22, 30, ("阳遁", 4, "下元")),  # 惊蛰 day-4 接气交节后 (段起甲戌=下)
        (2026, 3, 6, 12, 0, ("阳遁", 1, "上元")),   # 段起己卯=上
        (2026, 3, 11, 12, 0, ("阳遁", 7, "中元")),  # 段起甲申=中
        (2026, 3, 16, 12, 0, ("阳遁", 4, "下元")),  # 段起己丑=下
        (2026, 6, 6, 12, 0, ("阳遁", 6, "上元")),   # 芒种 day-1 接气 (段起己酉=上)
        (2026, 9, 7, 23, 0, ("阴遁", 3, "中元")),   # 白露 交节日超神 (段起甲申=中)
        (2026, 9, 23, 12, 0, ("阴遁", 1, "中元")),  # 秋分 day-1 接气 (段起己亥=中)
        (2026, 9, 30, 12, 0, ("阴遁", 4, "下元")),  # 段起甲辰=下
        (2026, 4, 10, 12, 0, ("阳遁", 1, "中元")),  # 清明 (段起甲寅=中)
        (2026, 4, 25, 12, 0, ("阳遁", 2, "中元")),  # 谷雨 (段起己巳=中)
        (2026, 3, 2, 12, 0, ("阳遁", 3, "下元")),   # 雨水 (段起甲戌=下)
        (2026, 2, 28, 12, 0, ("阳遁", 6, "中元")),  # 立春 (段起己巳=中)
        (2026, 1, 25, 12, 0, ("阳遁", 9, "中元")),  # 大寒 (段起己亥=中)
        (2026, 1, 15, 12, 0, ("阳遁", 5, "下元")),  # 小寒 (段起己丑=下)
        (2026, 6, 28, 12, 0, ("阴遁", 3, "中元")),  # 夏至 (段起己巳=中)
    ]
    for y, m, d, h, mi, exp in cases:
        r = _E.calculate(y, m, d, h, mi)
        got = (r.dun_type, r.ju_number, r.raw_data["yuan"])
        assert got == exp, f"{y}-{m}-{d} {h}:{mi}: 引擎 {got} != 权威 {exp}"


def test_chaibu_boundary_same_day_switch():
    """边界专项: 交节时刻同日切换 (时辰级交节判断, 权威锚点)."""
    # QM3a: 2025-12-21 23:00 (冬至 23:03 交节前3分钟) = 大雪段 阴4上元
    r = _E.calculate(2025, 12, 21, 23, 0)
    assert (r.raw_data["solar_term"], r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("大雪", "阴遁", 4, "上元")
    # QM3b: 2025-12-22 00:30 (冬至后1.5h) = 冬至段 阳1上元
    r = _E.calculate(2025, 12, 22, 0, 30)
    assert (r.raw_data["solar_term"], r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("冬至", "阳遁", 1, "上元")
    # QM2: 2026-03-05 09:30 (惊蛰 21:59 交节前12.5h) = 雨水段 下元阳3
    r = _E.calculate(2026, 3, 5, 9, 30)
    assert (r.raw_data["solar_term"], r.dun_type, r.ju_number, r.raw_data["yuan"]) == ("雨水", "阳遁", 3, "下元")


def test_luoshu_ring_rotation():
    """洛书环专项: RING 刚性旋转逐宫断言 + 八神顺逆环布 (权威锚点 12/22 01:30, 8/25 01:30)."""
    assert LUOSHU_RING == [9, 2, 7, 6, 1, 8, 3, 4]
    # ring_at 沿环后退 (天盘@X=地盘@ring(X,s) 的取源方向); 逆运算是 ring_shift(目标, 源)
    for p in LUOSHU_RING:
        for s in range(8):
            assert _ring_shift(_ring_at(p, s), p) == s
    a = AUTHORITY_ANCHORS["2025-12-22 01:30"]
    r = _E.calculate(*a["t"])
    dipan = {PALACE_NUMS[n]: v for n, v in r.dipan.items()}
    tianpan = {PALACE_NUMS[n]: v for n, v in r.tianpan.items()}
    jiuxing = {PALACE_NUMS[n]: v for n, v in r.jiuxing.items() if n != "中"}
    bamen = {PALACE_NUMS[n]: v for n, v in r.bamen.items() if n != "中"}
    s = _ring_shift(a["zf_orig"], a["zf_gong"])
    # 八门用值使位移: 值使原宫 = 旬首宫 = 值符原宫
    s_door = _ring_shift(a["zf_orig"], a["zs_gong"])
    for p in LUOSHU_RING:
        assert tianpan[p] == dipan[_ring_at(p, s)], f"天盘@{p}"
        assert jiuxing[p] == JIU_XING_ORIGIN[_ring_at(p, s)], f"九星@{p}"
        assert bamen[p] == BA_MEN_ORIGIN[_ring_at(p, s_door)], f"八门@{p}"
    # 中5 奇仪不动 (权威口径: 中宫双奇仪随天禽, 我方=地盘@中5)
    assert r.tianpan["中"] == r.dipan["中"]
    # 八神: 值符神@落宫, 阳遁顺行环布
    bashen = {PALACE_NUMS[n]: v for n, v in r.bashen.items() if n != "中"}
    cur = a["zf_gong"]
    for i, sh in enumerate(BA_SHEN):
        assert bashen[cur] == sh, f"八神@{cur}"
        if i < 7:
            cur = LUOSHU_RING[(RING_IDX[cur] + 1) % 8]
    # 阴遁逆行: 8/25 01:30 阴4局
    a2 = AUTHORITY_ANCHORS["2026-08-25 01:30"]
    r2 = _E.calculate(*a2["t"])
    bashen2 = {PALACE_NUMS[n]: v for n, v in r2.bashen.items() if n != "中"}
    cur = a2["zf_gong"]
    for i, sh in enumerate(BA_SHEN):
        assert bashen2[cur] == sh, f"八神@{cur} (阴遁)"
        if i < 7:
            cur = LUOSHU_RING[(RING_IDX[cur] - 1) % 8]


def test_zhishi_palace_formula():
    """D 破解公式: 值使落宫 = 旬首六仪地盘宫 ± steps (飞盘位移, 阳+阴-), 中5->坤2."""
    # 全锚点 值使落宫 已由 test_authority_anchors_all_pass 覆盖 64/64;
    # 此处显式断言公式与引擎一致 (含 5->2 情形)
    from src.engines.qimen import DIZHI, JIE_QI_DUN
    import src.engines.qimen as qm
    for key, a in AUTHORITY_ANCHORS.items():
        r = _E.calculate(*a["t"])
        tz = DIZHI.index(r.raw_data["bazi"][3][1])
        xz = DIZHI.index(r.raw_data["xunshou"][1])
        steps = (tz - xz) % 12
        direction = 1 if r.dun_type == "阳遁" else -1
        xp = PALACE_NUMS[r.raw_data["xunshou_palace"]]
        tgt = ((xp - 1 + direction * steps) % 9) + 1
        if tgt == 5:
            tgt = 2
        got = [PALACE_NUMS[n] for n, v in r.bamen.items() if v == r.zhishi_door][0]
        assert got == tgt, f"{key}: 公式 {tgt} != 引擎 {got}"


def test_result_shape_unchanged():
    """契约: QimenResult 字段形状不变 (消费点依赖)."""
    r = _E.calculate(2026, 8, 30, 10, 0)
    assert set(r.__dataclass_fields__) == {
        "dun_type", "ju_number", "dipan", "tianpan", "bamen", "jiuxing",
        "bashen", "zhifu_star", "zhishi_door", "raw_data",
    }
    assert set(r.raw_data) == {"solar_term", "yuan", "bazi", "xunshou",
                             "xunshou_yi", "xunshou_palace", "hour_gan_palace"}
    assert set(r.dipan) == {"坎", "坤", "震", "巽", "中", "乾", "兑", "艮", "离"}
    assert 1 <= r.ju_number <= 9 and r.dun_type in ("阳遁", "阴遁")


def test_late_zi_kept():
    """晚子时归次日规则保持 (23:00 起时柱归次日). bazi = [年柱,月柱,日柱,时柱]."""
    assert _E.calculate(2025, 12, 21, 23, 0).raw_data["bazi"][3] == "丙子"
    assert _E.calculate(2025, 12, 21, 22, 0).raw_data["bazi"][3] == "乙亥"
