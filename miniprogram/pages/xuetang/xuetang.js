// 易理学堂 — 知识课程
const api = require('../../utils/api');

const DEMO_TOPICS = [
  {
    id: 'bazi',
    icon: '',
    name: '八字入门',
    description: '了解四柱八字的组成原理，学会排盘与解读基本框架。',
    lessonCount: 6,
  },
  {
    id: 'wuxing',
    icon: '🔥',
    name: '五行学说',
    description: '深入理解金木水火土的相生相克与在命理中的运用。',
    lessonCount: 5,
  },
  {
    id: 'shengxiao',
    icon: '🐉',
    name: '生肖文化',
    description: '十二生肖的起源、性格特征与婚配宜忌。',
    lessonCount: 4,
  },
  {
    id: 'fengshui',
    icon: '🏠',
    name: '风水基础',
    description: '阳宅风水入门，学会布局家居与办公空间的旺运法则。',
    lessonCount: 7,
  },
];

const DEMO_LESSONS = {
  bazi: {
    icon: '',
    title: '什么是八字？',
    topicLabel: '八字入门',
    content: `八字，又称四柱命理，是中国传统命理学的核心方法之一。

一、八字的构成

八字由"年、月、日、时"四柱组成，每柱包含一个天干和一个地支，共八个字，故称"八字"。

• 年柱：代表出生年份的天干地支
• 月柱：代表出生月份的天干地支
• 日柱：代表出生日期的天干地支
• 时柱：代表出生时辰的天干地支

二、天干地支

天干共十位：甲、乙、丙、丁、戊、己、庚、辛、壬、癸
地支共十二位：子、丑、寅、卯、辰、巳、午、未、申、酉、戌、亥

三、五行属性

每个天干地支都有对应的五行属性：
• 甲乙属木，丙丁属火，戊己属土，庚辛属金，壬癸属水
• 寅卯属木，巳午属火，申酉属金，亥子属水，辰戌丑未属土

四、排盘方法

传统的八字排盘需要知道准确的出生年月日时（以农历为准）。一般步骤为：
1. 确定年柱 — 以立春为分界
2. 确定月柱 — 根据年干推算月干
3. 确定日柱 — 通过日干支公式计算
4. 确定时柱 — 根据日干推算时干

掌握了这些基础，就可以开始解读命盘中的五行强弱、十神关系与流年运势了。`,
  },
  wuxing: {
    icon: '🔥',
    title: '五行生克与命理应用',
    topicLabel: '五行学说',
    content: `五行学说是中国传统哲学的基础理论之一，它将宇宙万物归纳为五种基本元素：金、木、水、火、土。

一、五行的基本属性

• 木：具有生长、升发、条达的特性。对应春季、东方、青色、肝脏。
• 火：具有温热、向上、光明的特性。对应夏季、南方、赤色、心脏。
• 土：具有承载、化生、受纳的特性。对应季末、中央、黄色、脾脏。
• 金：具有清洁、收敛、肃降的特性。对应秋季、西方、白色、肺脏。
• 水：具有寒凉、滋润、向下的特性。对应冬季、北方、黑色、肾脏。

二、相生关系（相互滋生）

木生火，火生土，土生金，金生水，水生木。

三、相克关系（相互制约）

木克土，土克水，水克火，火克金，金克木。

四、在命理中的运用

八字命理通过分析日主（出生日的天干）的五行强弱，判断命局中五行的平衡状况。当某种五行过旺或过弱时，就需要通过"用神"来调节。

例如：日主为甲木（阳木），生于秋季金旺之时，木被金克，就需要水来通关（水生木，金生水），此时水就是"用神"。

了解五行的生克制化，是读懂命盘的第一步。`,
  },
  shengxiao: {
    icon: '🐉',
    title: '十二生肖的性格与配对',
    topicLabel: '生肖文化',
    content: `十二生肖是中国传统文化中重要的组成部分，每个人根据自己的出生年份对应一个生肖属相。

一、十二生肖顺序

鼠、牛、虎、兔、龙、蛇、马、羊、猴、鸡、狗、猪

每个生肖对应一个地支：
子鼠、丑牛、寅虎、卯兔、辰龙、巳蛇、午马、未羊、申猴、酉鸡、戌狗、亥猪

二、生肖性格简述

• 鼠：机智灵活，善于交际，警觉性高
• 牛：勤劳踏实，任劳任怨，意志坚定
• 虎：勇敢自信，领导力强，有冒险精神
• 兔：温和细腻，善解人意，有艺术气质
• 龙：大气磅礴，充满活力，志向高远
• 蛇：智慧深沉，直觉敏锐，处事谨慎
• 马：热情奔放，追求自由，行动力强
• 羊：温柔善良，富有同情心，艺术天赋
• 猴：聪明活泼，适应力强，多才多艺
• 鸡：自信果断，处事条理，精益求精
• 狗：忠诚正直，责任心强，正义感足
• 猪：朴实厚道，豁达乐观，福气深厚

三、生肖配对原理

生肖配对以"六合"、"三合"为吉配，"六冲"、"六害"为需谨慎的组合。

六合：鼠牛合、虎猪合、兔狗合、龙鸡合、蛇猴合、马羊合

三合：猴鼠龙合、蛇鸡牛合、虎马狗合、猪兔羊合

六冲：鼠马冲、牛羊冲、虎猴冲、兔鸡冲、龙狗冲、蛇猪冲

需要注意的是，生肖只是婚配参考的一个维度，完整的合婚还需结合八字整体分析。`,
  },
  fengshui: {
    icon: '🏠',
    title: '阳宅风水入门要点',
    topicLabel: '风水基础',
    content: `风水，又称堪舆，是中国传统环境学说。阳宅风水主要研究人类居住环境对运势的影响。

一、核心原则

1. 藏风聚气 — 住宅应当能够聚集吉祥之气，避免穿堂风直吹
2. 阴阳平衡 — 光线明暗、温度冷暖、动静区域需协调
3. 五行调和 — 家居布置中五种元素要和谐共处

二、大门风水

大门是住宅的"气口"，至关重要：
• 大门不宜正对电梯、楼梯或长走廊（形成"冲煞"）
• 大门不宜正对厨房门或卫生间门
• 大门内外保持整洁明亮，忌堆放杂物

三、客厅风水

客厅是家庭聚气之所：
• 客厅宜宽敞明亮，位于房屋前半部分
• 沙发宜靠实墙摆放，背后不宜空
• 天花板不宜过低，以免产生压抑感
• 绿植可以增加生气，但避免带刺植物

四、卧室风水

卧室关乎健康和感情：
• 床头宜靠实墙，不宜靠窗或正对门
• 镜子不宜正对床
• 卧室色调以柔和为主，不宜过于鲜艳
• 卧室不宜过大（"屋大人少"为风水之忌）

五、厨房与卫生间

• 厨房属火，宜在房屋凶位（压煞）
• 卫生间属水，不宜在房屋中心位置
• 厨房门不宜正对卫生间门（水火相冲）

以上是风水的入门知识，实际应用中还需结合具体户型、方位和居住者的八字进行综合分析。`,
  },
};

Page({
  data: {
    topics: [],
    currentTopic: null,
    currentTopicName: '',
    lesson: null,
    loading: false,
    showLesson: false,
  },

  onLoad() {
    this.loadTopics();
  },

  onShow() {
    // 从详情返回时不再重新加载
    if (this.data.showLesson) return;
    if (this.data.topics.length === 0) {
      this.loadTopics();
    }
  },

  // ---- 加载话题列表 ----
  loadTopics() {
    this.setData({ loading: true });

    api.getXuetangTopics()
      .then((res) => {
        const topics = res.topics || [];
        if (topics.length === 0) {
          this.setDemoTopics();
          return;
        }
        this.setData({ topics, loading: false });
      })
      .catch(() => {
        wx.showToast({
          title: '加载失败，已显示示例课程',
          icon: 'none',
          duration: 2000,
        });
        this.setDemoTopics();
      })
      .finally(() => {
        this.setData({ loading: false });
      });
  },

  setDemoTopics() {
    this.setData({
      topics: DEMO_TOPICS,
      loading: false,
    });
  },

  // ---- 选择话题，加载课程 ----
  selectTopic(e) {
    const topicId = e.currentTarget.dataset.topic;
    const topic = this.data.topics.find(t => t.id === topicId);
    if (!topic) return;

    this.setData({
      currentTopic: topic,
      currentTopicName: topic.name,
      showLesson: true,
      lesson: null,
    });

    api.getXuetangLesson(topicId)
      .then((res) => {
        if (res && res.lesson) {
          this.setData({ lesson: res.lesson });
        } else {
          this.setDemoLesson(topicId);
        }
      })
      .catch(() => {
        wx.showToast({
          title: '课程加载失败，已显示示例内容',
          icon: 'none',
          duration: 2000,
        });
        this.setDemoLesson(topicId);
      });
  },

  setDemoLesson(topicId) {
    const lesson = DEMO_LESSONS[topicId];
    if (lesson) {
      this.setData({ lesson });
    }
  },

  // ---- 返回列表 ----
  backToList() {
    this.setData({
      showLesson: false,
      currentTopic: null,
      currentTopicName: '',
      lesson: null,
    });
  },

  // ---- 分享 ----
  onShareAppMessage() {
    if (this.data.showLesson && this.data.lesson) {
      return {
        title: `易理学堂 - ${this.data.lesson.title}`,
        path: '/pages/xuetang/xuetang',
      };
    }
    return {
      title: '易理学堂 - 从零开始学易理',
      path: '/pages/xuetang/xuetang',
    };
  },
});
