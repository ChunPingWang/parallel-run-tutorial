// =====================================================================
// build-deck.js  -  平行測試 PoC 管理層簡報
// 設計系統：FB / Carbon 色盤（0F62FE 主藍、0C2D4F navy、009C97 teal、
//           F4693D 橘）；標題 IBM Plex Sans 粗體、內文 Arial
// =====================================================================
const pptxgen = require("pptxgenjs");

const C = {
  titleBlue: "0A61FE",
  blue: "0F62FE",
  navy: "0C2D4F",
  dark: "001B2C",
  teal: "009C97",
  orange: "F4693D",
  ink: "161616",
  gray: "525252",
  grayLt: "6F6F6F",
  cardGray: "F4F4F4",
  blueLt: "E5F6FF",
  blueLt2: "D0E2FF",
  white: "FFFFFF",
  line: "DDE1E6",
};

const F = { head: "IBM Plex Sans", body: "Arial" };

const M = 0.55;            // 左右邊界
const W = 13.333;
const CW = W - M * 2;      // 內容寬 12.233

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";   // 13.333 x 7.5
pres.author = "應用架構";
pres.title = "新舊應用平行測試 PoC";

const shadow = () => ({ type: "outer", color: "0C2D4F", blur: 8, offset: 1, angle: 90, opacity: 0.10 });

// --------------------------------------------------------------- 共用元件
function pageTitle(s, text, sub) {
  s.addText(text, {
    x: M, y: 0.42, w: CW, h: 0.55, fontFace: F.head, fontSize: 27,
    bold: true, color: C.titleBlue, margin: 0, valign: "middle",
  });
  if (sub) {
    s.addText(sub, {
      x: M, y: 1.00, w: CW, h: 0.32, fontFace: F.body, fontSize: 12.5,
      color: C.gray, margin: 0, valign: "middle",
    });
  }
}

function footer(s, n) {
  s.addText("新舊應用平行測試 PoC　|　應用架構", {
    x: M, y: 6.92, w: 8, h: 0.3, fontFace: F.body, fontSize: 9,
    color: C.grayLt, margin: 0,
  });
  s.addText(String(n), {
    x: W - M - 0.6, y: 6.92, w: 0.6, h: 0.3, fontFace: F.body, fontSize: 9,
    color: C.grayLt, align: "right", margin: 0,
  });
}

function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.05,
    fill: { color: o.fill || C.white },
    line: { color: o.line || C.line, width: 1 },
    shadow: o.flat ? undefined : shadow(),
  });
}

function chip(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: 0.26, rectRadius: 0.03,
    fill: { color: o.color }, line: { color: o.color, width: 0 },
  });
  s.addText(o.text, {
    x: o.x, y: o.y, w: o.w, h: 0.26, fontFace: F.body, fontSize: 8.5,
    bold: true, color: C.white, align: "center", valign: "middle", margin: 0,
  });
}

function numCircle(s, o) {
  s.addShape(pres.ShapeType.ellipse, {
    x: o.x, y: o.y, w: 0.42, h: 0.42,
    fill: { color: o.color }, line: { color: o.color, width: 0 },
  });
  s.addText(o.n, {
    x: o.x, y: o.y, w: 0.42, h: 0.42, fontFace: F.head, fontSize: 13,
    bold: true, color: C.white, align: "center", valign: "middle", margin: 0,
  });
}

// =====================================================================
// 1  封面
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.dark };

  s.addText("新舊應用平行測試 PoC", {
    x: M, y: 1.75, w: CW, h: 0.95, fontFace: F.head, fontSize: 42,
    bold: true, color: C.white, margin: 0, valign: "middle",
  });
  s.addText("以生產真實流量驗證新舊系統的功能等價性與性能容量", {
    x: M, y: 2.72, w: CW, h: 0.42, fontFace: F.body, fontSize: 16,
    color: C.blueLt2, margin: 0, valign: "middle",
  });
  s.addText("舊應用（虛擬機）　↔　新應用（容器 / Kubernetes）", {
    x: M, y: 3.14, w: CW, h: 0.36, fontFace: F.body, fontSize: 12.5,
    color: C.grayLt, margin: 0, valign: "middle",
  });

  const stats = [
    { v: "6 週", l: "PoC 期程" },
    { v: "零", l: "正線程式改動" },
    { v: "6 道", l: "階段 Gate 把關" },
  ];
  stats.forEach((it, i) => {
    const x = M + i * 3.05;
    s.addText(it.v, {
      x, y: 4.35, w: 2.7, h: 0.72, fontFace: F.head, fontSize: 40,
      bold: true, color: C.teal, margin: 0, valign: "middle",
    });
    s.addText(it.l, {
      x, y: 5.07, w: 2.7, h: 0.3, fontFace: F.body, fontSize: 11.5,
      color: C.blueLt2, margin: 0,
    });
  });

  s.addText("提報單位：應用架構　|　2026-07-25", {
    x: M, y: 6.55, w: CW, h: 0.3, fontFace: F.body, fontSize: 10.5,
    color: C.grayLt, margin: 0,
  });

  s.addNotes(
    "本次提報目的是取得 PoC 啟動核准，不是技術方案審查。\n" +
    "三個數字是全場重點：6 週、正線零改動、6 道 Gate。"
  );
}

// =====================================================================
// 2  一頁摘要
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "一頁摘要", "給決策者的三分鐘版本");

  // 結論框
  card(s, { x: M, y: 1.55, w: CW, h: 1.02, fill: C.blueLt });
  s.addText("結論", {
    x: M + 0.28, y: 1.70, w: 1.0, h: 0.28, fontFace: F.body, fontSize: 10.5,
    bold: true, color: C.blue, margin: 0,
  });
  s.addText(
    "以生產真實流量離線驗證新應用，正線不做任何改動；分四階段推進，" +
    "每階段設 Gate，未達標即停止並回報，不以商業時程壓力放行。",
    {
      x: M + 0.28, y: 1.98, w: CW - 0.56, h: 0.5, fontFace: F.body, fontSize: 13,
      color: C.navy, margin: 0, lineSpacingMultiple: 1.15,
    }
  );

  const items = [
    {
      n: "1", c: C.blue, t: "要解決什麼",
      b: "新舊系統的行為差異，在測試環境用人工設計的案例往往測不出來。真實流量的邊界情境才是核心系統汰換的主要風險來源。",
    },
    {
      n: "2", c: C.teal, t: "怎麼做",
      b: "在舊系統上以唯讀方式側錄生產流量，遮蔽個資後於隔離環境重放至新系統，離線比對兩邊回應差異，並以倍率放大驗證容量。",
    },
    {
      n: "3", c: C.navy, t: "為什麼安全",
      b: "正線流量路徑完全不變，回退僅需停止側錄程式。新系統連的是影子資料庫與模擬的外部系統，並以網路層阻斷作為最後防線。",
    },
    {
      n: "4", c: C.orange, t: "需要您核准",
      b: "法遵送審（生產資料側錄）、人力與環境資源、以及授權「Gate 未過即停止」的決策機制。詳見最後一頁。",
    },
  ];

  const cw = (CW - 0.36 * 3) / 4;
  items.forEach((it, i) => {
    const x = M + i * (cw + 0.36);
    card(s, { x, y: 2.85, w: cw, h: 3.2 });
    numCircle(s, { x: x + 0.26, y: 3.10, n: it.n, color: it.c });
    s.addText(it.t, {
      x: x + 0.26, y: 3.66, w: cw - 0.52, h: 0.34, fontFace: F.head, fontSize: 14.5,
      bold: true, color: C.navy, margin: 0, valign: "middle",
    });
    s.addText(it.b, {
      x: x + 0.26, y: 4.06, w: cw - 0.52, h: 2.1, fontFace: F.body, fontSize: 11,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.22, valign: "top",
    });
  });

  footer(s, 2);
  s.addNotes("若只講一頁，講這一頁。第 4 張卡片對應最後一頁的核准事項。");
}

// =====================================================================
// 3  為什麼需要平行測試
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "為什麼既有的測試方式不夠", "核心系統汰換的風險，多半不在「想得到的案例」裡");

  const colW = (CW - 0.45) / 2;

  // 左：盲點
  card(s, { x: M, y: 1.62, w: colW, h: 4.55, fill: C.white });
  chip(s, { x: M + 0.3, y: 1.88, w: 1.55, text: "現況的盲點", color: C.orange });
  s.addText("既有驗證方式", {
    x: M + 0.3, y: 2.24, w: colW - 0.6, h: 0.38, fontFace: F.head, fontSize: 17,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });

  const left = [
    ["案例來自人的想像", "UAT 與迴歸測試涵蓋的是「我們想得到」的情境。真實世界的資料組合遠比測試案例多樣。"],
    ["測試資料不等於生產資料", "歷史髒資料、早年版本留下的特殊值、跨系統的邊界狀態，測試環境通常沒有。"],
    ["比對靠人工抽樣", "上線後靠人工對帳抽查，發現差異時交易已經完成，補救成本高。"],
    ["容量靠推估", "壓測腳本的流量形狀與生產不同，尖峰時的真實瓶頸經常測不出來。"],
  ];
  left.forEach((it, i) => {
    const y = 2.75 + i * 0.85;
    s.addShape(pres.ShapeType.ellipse, {
      x: M + 0.32, y: y + 0.06, w: 0.14, h: 0.14,
      fill: { color: C.orange }, line: { color: C.orange, width: 0 },
    });
    s.addText(it[0], {
      x: M + 0.58, y: y, w: colW - 0.9, h: 0.26, fontFace: F.body, fontSize: 12,
      bold: true, color: C.ink, margin: 0, valign: "middle",
    });
    s.addText(it[1], {
      x: M + 0.58, y: y + 0.26, w: colW - 0.9, h: 0.52, fontFace: F.body, fontSize: 10.5,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.15,
    });
  });

  // 右：補上什麼
  const rx = M + colW + 0.45;
  card(s, { x: rx, y: 1.62, w: colW, h: 4.55, fill: C.blueLt });
  chip(s, { x: rx + 0.3, y: 1.88, w: 1.55, text: "平行測試補上", color: C.teal });
  s.addText("以真實流量驗證", {
    x: rx + 0.3, y: 2.24, w: colW - 0.6, h: 0.38, fontFace: F.head, fontSize: 17,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });

  const right = [
    ["案例來自真實交易", "生產流量本身就是最完整的測試案例集合，涵蓋人想不到的組合。"],
    ["用生產資料驗證", "以正式庫的副本為基礎，髒資料與特殊值一併納入驗證。"],
    ["自動比對每一筆", "新舊回應逐筆比對，扣除合理噪音後量化為一致率，作為推進與否的客觀依據。"],
    ["以真實流量放大驗容量", "把錄下來的流量放大 2 倍、5 倍重放，量出新系統的實際容量曲線與瓶頸。"],
  ];
  right.forEach((it, i) => {
    const y = 2.75 + i * 0.85;
    s.addShape(pres.ShapeType.ellipse, {
      x: rx + 0.32, y: y + 0.06, w: 0.14, h: 0.14,
      fill: { color: C.teal }, line: { color: C.teal, width: 0 },
    });
    s.addText(it[0], {
      x: rx + 0.58, y: y, w: colW - 0.9, h: 0.26, fontFace: F.body, fontSize: 12,
      bold: true, color: C.ink, margin: 0, valign: "middle",
    });
    s.addText(it[1], {
      x: rx + 0.58, y: y + 0.26, w: colW - 0.9, h: 0.52, fontFace: F.body, fontSize: 10.5,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.15,
    });
  });

  s.addText(
    "平行測試不取代 UAT，是補上 UAT 涵蓋不到的部分。兩者並行。",
    { x: M, y: 6.32, w: CW, h: 0.3, fontFace: F.body, fontSize: 10.5,
      italic: true, color: C.grayLt, margin: 0 }
  );

  footer(s, 3);
  s.addNotes("預期提問：那 UAT 還要做嗎？答：要，這是補強不是取代，已寫在頁尾。");
}

// =====================================================================
// 4  方案選型
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "方案選型：為何採「錄製重放」而非「即時鏡像」", "兩者皆為業界成熟做法，差別在對正線的風險");

  const rows = [
    ["評估項目", "錄製重放（採用）", "即時鏡像（備案）"],
    ["對正線的改動", "無，唯讀側錄網路封包", "須在流量路徑插入閘道"],
    ["新增故障點", "無", "有，閘道本身"],
    ["回退方式", "停止側錄程式，立即生效", "調整設定並重載，約 30 秒"],
    ["能否放大流量驗容量", "可放大 2x / 5x", "不可，僅能複製 1 倍"],
    ["差異發現時效", "分鐘至小時級", "秒級"],
  ];

  const colX = [M, M + 4.6, M + 8.45];
  const colW2 = [4.6, 3.85, 3.78];
  const rowH = 0.56;
  const top = 1.62;

  rows.forEach((r, ri) => {
    const y = top + ri * rowH;
    const isHead = ri === 0;
    r.forEach((cell, ci) => {
      s.addShape(pres.ShapeType.rect, {
        x: colX[ci], y, w: colW2[ci], h: rowH,
        fill: { color: isHead ? C.navy : (ci === 1 ? C.blueLt : C.white) },
        line: { color: isHead ? C.navy : C.line, width: 1 },
      });
      s.addText(cell, {
        x: colX[ci] + 0.18, y, w: colW2[ci] - 0.36, h: rowH,
        fontFace: F.body, fontSize: isHead ? 11.5 : 11.5,
        bold: isHead || ci === 0, color: isHead ? C.white : (ci === 1 ? C.navy : C.gray),
        valign: "middle", margin: 0,
      });
    });
  });

  const cy = top + rows.length * rowH + 0.35;
  card(s, { x: M, y: cy, w: CW, h: 1.32, fill: C.white });
  chip(s, { x: M + 0.3, y: cy + 0.24, w: 1.0, text: "決策", color: C.blue });
  s.addText("採錄製重放為主方案，即時鏡像列為備案", {
    x: M + 1.45, y: cy + 0.2, w: CW - 1.8, h: 0.34, fontFace: F.head, fontSize: 16,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });
  s.addText(
    "決定性因素是「正線零改動」與「可放大倍率」。容量驗證只能走錄製重放 —— " +
    "即時鏡像無法產生超過 1 倍的負載。唯一代價是差異回饋較慢，對 PoC 而言可接受。",
    { x: M + 1.45, y: cy + 0.58, w: CW - 1.8, h: 0.6, fontFace: F.body, fontSize: 11.5,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.2 }
  );

  footer(s, 4);
  s.addNotes("完整選型比較（含 Envoy / Istio / Diffy 等）在 ADR-001，此處只列決策關鍵項。");
}

// =====================================================================
// 5  架構總覽
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "運作方式", "四個階段，正線流量路徑完全不變");

  const stages = [
    { n: "1", t: "側錄", c: C.navy,
      lines: ["舊應用（虛擬機）", "唯讀監聽網卡", "取樣 5% 起"] },
    { n: "2", t: "遮蔽", c: C.blue,
      lines: ["帳號、身分證、卡號", "一致性雜湊處理", "法遵核准後始執行"] },
    { n: "3", t: "重放", c: C.teal,
      lines: ["新應用（容器）", "影子資料庫", "外部系統以模擬取代"] },
    { n: "4", t: "比對", c: C.orange,
      lines: ["逐筆比對回應", "扣除合理噪音", "產出一致率與 Gate"] },
  ];

  const bw = 2.78, gap = (CW - bw * 4) / 3;
  stages.forEach((st, i) => {
    const x = M + i * (bw + gap);
    card(s, { x, y: 1.68, w: bw, h: 2.5 });
    numCircle(s, { x: x + 0.24, y: 1.92, n: st.n, color: st.c });
    s.addText(st.t, {
      x: x + 0.78, y: 1.92, w: bw - 1.0, h: 0.42, fontFace: F.head, fontSize: 18,
      bold: true, color: C.navy, margin: 0, valign: "middle",
    });
    st.lines.forEach((ln, j) => {
      s.addText(ln, {
        x: x + 0.26, y: 2.52 + j * 0.42, w: bw - 0.52, h: 0.38,
        fontFace: F.body, fontSize: 11, color: C.gray, margin: 0, valign: "middle",
      });
    });
    if (i < 3) {
      s.addShape(pres.ShapeType.rightArrow, {
        x: x + bw + 0.04, y: 2.78, w: gap - 0.08, h: 0.3,
        fill: { color: C.blueLt2 }, line: { color: C.blueLt2, width: 0 },
      });
    }
  });

  // 重點帶
  card(s, { x: M, y: 4.42, w: CW, h: 0.86, fill: C.blueLt });
  s.addText(
    "正線只多了一個唯讀的側錄程式。它不介入流量、不改變回應、不新增故障點；要停止時直接關掉即可。",
    { x: M + 0.3, y: 4.42, w: CW - 0.6, h: 0.86, fontFace: F.body, fontSize: 13,
      bold: true, color: C.navy, margin: 0, valign: "middle" }
  );

  const notes = [
    ["側錄程式的資源佔用", "上線前先在壓測環境實測，確認正線回應時間增幅低於 2% 才可於生產啟用。"],
    ["新舊環境不同的處理", "舊系統在虛擬機、新系統在容器，兩者資源模型不同，性能以應用內部計時為準，不直接比端到端時間。"],
  ];
  notes.forEach((it, i) => {
    const x = M + i * (CW / 2 + 0.2);
    const w = CW / 2 - 0.2;
    s.addText(it[0], {
      x, y: 5.5, w, h: 0.28, fontFace: F.body, fontSize: 11.5,
      bold: true, color: C.navy, margin: 0, valign: "middle",
    });
    s.addText(it[1], {
      x, y: 5.78, w, h: 0.72, fontFace: F.body, fontSize: 10.5,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.18,
    });
  });

  footer(s, 5);
  s.addNotes("若被問「會不會影響正線」，重點帶那句就是答案。資源佔用的實測是 W1 的 Gate 項目。");
}

// =====================================================================
// 6  最高風險與三層防護
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "最高風險：影子交易外洩到正式環境", "若隔離失效，後果是重複記帳、重複扣款、重複發送通知");

  const layers = [
    { n: "1", t: "應用層", c: C.blue, sub: "新系統自我約束",
      b: "新應用辨識到影子流量標記後進入「唯讀演練」模式：不發送通知、不呼叫外部收單清算、交易不寫入主檔。" },
    { n: "2", t: "資源層", c: C.teal, sub: "連的不是正式資源",
      b: "新應用連的是影子資料庫（正式庫副本），外部系統一律以模擬服務取代，訊息佇列走獨立主題。" },
    { n: "3", t: "網路層", c: C.navy, sub: "最後一道防線",
      b: "以網路政策封鎖新應用對外連線，僅開放隔離環境內部。即使前兩層設定有誤，連線也會直接被拒絕。" },
  ];

  const cw = (CW - 0.4 * 2) / 3;
  layers.forEach((l, i) => {
    const x = M + i * (cw + 0.4);
    card(s, { x, y: 1.68, w: cw, h: 2.85 });
    numCircle(s, { x: x + 0.28, y: 1.96, n: l.n, color: l.c });
    s.addText(l.t, {
      x: x + 0.84, y: 1.96, w: cw - 1.1, h: 0.42, fontFace: F.head, fontSize: 18,
      bold: true, color: C.navy, margin: 0, valign: "middle",
    });
    s.addText(l.sub, {
      x: x + 0.28, y: 2.5, w: cw - 0.56, h: 0.3, fontFace: F.body, fontSize: 11,
      bold: true, color: l.c, margin: 0, valign: "middle",
    });
    s.addText(l.b, {
      x: x + 0.28, y: 2.84, w: cw - 0.56, h: 1.5, fontFace: F.body, fontSize: 11,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.22,
    });
  });

  card(s, { x: M, y: 4.78, w: CW, h: 1.45, fill: C.white });
  chip(s, { x: M + 0.3, y: 5.04, w: 1.35, text: "關鍵原則", color: C.orange });
  s.addText("三層都要有，而且三層都要實際驗證過", {
    x: M + 1.8, y: 4.98, w: CW - 2.1, h: 0.34, fontFace: F.head, fontSize: 16,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });
  s.addText(
    "只靠應用層自我約束是不夠的 —— 設定錯誤或程式分支遺漏時毫無徵兆。" +
    "網路層阻斷會讓錯誤「立即失敗」而非靜默寫入。此項列為第一週的 Gate 阻擋事項，" +
    "須在新系統內實際嘗試連線正式資料庫並確認被拒絕，留存證據後方可進入下一階段。",
    { x: M + 1.8, y: 5.36, w: CW - 2.1, h: 0.75, fontFace: F.body, fontSize: 11.5,
      color: C.gray, margin: 0, lineSpacingMultiple: 1.2 }
  );

  footer(s, 6);
  s.addNotes("這一頁是稽核與法遵最關心的內容。驗證證據會留存在檢核表 T-14。");
}

// =====================================================================
// 7  時程與 Gate
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "六週期程與階段把關", "每一週設一道 Gate，未達標即停在該階段，不往下推");

  const weeks = [
    { w: "W1", t: "環境建置", g: "連通性驗證通過\n隔離已實證", c: C.navy },
    { w: "W2", t: "噪音基線", g: "誤判率 < 1%", c: C.navy },
    { w: "W3", t: "唯讀查詢", g: "一致率 ≥ 99.9%\n正線影響 < 2%", c: C.blue },
    { w: "W4", t: "非核心寫入", g: "一致率 ≥ 99.95%", c: C.blue },
    { w: "W5", t: "核心交易", g: "一致率 = 100%", c: C.teal },
    { w: "W6", t: "性能驗證", g: "容量達標\n無資源節流", c: C.teal },
  ];

  const bw = (CW - 0.16 * 5) / 6;
  weeks.forEach((k, i) => {
    const x = M + i * (bw + 0.16);
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 1.68, w: bw, h: 0.5, rectRadius: 0.04,
      fill: { color: k.c }, line: { color: k.c, width: 0 },
    });
    s.addText(k.w, {
      x, y: 1.68, w: bw, h: 0.5, fontFace: F.head, fontSize: 15,
      bold: true, color: C.white, align: "center", valign: "middle", margin: 0,
    });
    card(s, { x, y: 2.24, w: bw, h: 2.15 });
    s.addText(k.t, {
      x: x + 0.12, y: 2.4, w: bw - 0.24, h: 0.6, fontFace: F.head, fontSize: 13.5,
      bold: true, color: C.navy, align: "center", valign: "middle", margin: 0,
    });
    s.addShape(pres.ShapeType.line, {
      x: x + 0.3, y: 3.02, w: bw - 0.6, h: 0,
      line: { color: C.line, width: 1 },
    });
    s.addText("Gate", {
      x: x + 0.12, y: 3.1, w: bw - 0.24, h: 0.24, fontFace: F.body, fontSize: 9,
      bold: true, color: k.c, align: "center", margin: 0,
    });
    s.addText(k.g, {
      x: x + 0.1, y: 3.34, w: bw - 0.2, h: 0.95, fontFace: F.body, fontSize: 10,
      color: C.gray, align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.15,
    });
  });

  card(s, { x: M, y: 4.68, w: CW, h: 1.55, fill: C.blueLt });
  chip(s, { x: M + 0.3, y: 4.94, w: 1.55, text: "需要授權", color: C.orange });
  s.addText("Gate 未過即停止，不因時程壓力放行", {
    x: M + 2.0, y: 4.88, w: CW - 2.3, h: 0.34, fontFace: F.head, fontSize: 16,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });
  s.addText(
    "此機制的價值完全取決於是否被確實執行。若某階段一致率未達標卻仍推進，" +
    "後續所有結論都失去意義。特別是核心交易階段要求 100% 一致 —— " +
    "此處不接受「差異可接受」的判斷，只接受「差異已釐清且已修正」。" +
    "六週為順利情形下的期程；任一 Gate 未過會延長，請以此為預期。",
    { x: M + 2.0, y: 5.26, w: CW - 2.3, h: 0.85, fontFace: F.body, fontSize: 11.5,
      color: C.navy, margin: 0, lineSpacingMultiple: 1.22 }
  );

  footer(s, 7);
  s.addNotes(
    "這是本次提報最重要的一個「請求」，不只是報告時程。\n" +
    "務必當場確認 Gate 未過時的決策權責歸屬。"
  );
}

// =====================================================================
// 8  資源需求
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "資源需求", "六週期程下的人力與環境投入");

  // 人力
  card(s, { x: M, y: 1.62, w: 7.15, h: 4.6 });
  s.addText("人力", {
    x: M + 0.3, y: 1.86, w: 3, h: 0.36, fontFace: F.head, fontSize: 17,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });

  const people = [
    ["應用架構", "0.5 人月", "方案設計、比對規則審定、Gate 判定"],
    ["平台維運", "1.0 人月", "環境建置、影子資料庫、網路隔離設定"],
    ["新應用開發", "0.5 人月", "演練模式實作、差異修正"],
    ["測試", "1.0 人月", "執行、人工抽樣複核、報告產出"],
    ["資安 / 法遵", "0.2 人月", "資料側錄審查、遮蔽規則確認"],
  ];
  const th = 0.66;
  people.forEach((p, i) => {
    const y = 2.4 + i * th;
    if (i > 0) {
      s.addShape(pres.ShapeType.line, {
        x: M + 0.3, y, w: 6.55, h: 0, line: { color: C.line, width: 1 },
      });
    }
    s.addText(p[0], {
      x: M + 0.3, y: y + 0.06, w: 1.75, h: 0.28, fontFace: F.body, fontSize: 11.5,
      bold: true, color: C.ink, margin: 0, valign: "middle",
    });
    s.addText(p[1], {
      x: M + 2.05, y: y + 0.06, w: 1.1, h: 0.28, fontFace: F.body, fontSize: 11.5,
      bold: true, color: C.blue, margin: 0, valign: "middle",
    });
    s.addText(p[2], {
      x: M + 0.3, y: y + 0.32, w: 6.5, h: 0.28, fontFace: F.body, fontSize: 10.5,
      color: C.gray, margin: 0, valign: "middle",
    });
  });

  s.addText("合計約 3.2 人月", {
    x: M + 0.3, y: 5.78, w: 6.5, h: 0.32, fontFace: F.head, fontSize: 14,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });

  // 環境
  const rx = M + 7.45;
  const rw = CW - 7.45;
  card(s, { x: rx, y: 1.62, w: rw, h: 2.35, fill: C.blueLt });
  s.addText("環境", {
    x: rx + 0.3, y: 1.84, w: 3, h: 0.34, fontFace: F.head, fontSize: 17,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });
  const env = [
    "影子資料庫（正式庫規格對等）",
    "新應用容器環境（2 節點，與舊系統規格對等）",
    "外部系統模擬服務",
    "錄製檔儲存（估 180 MB／小時，加密）",
  ];
  env.forEach((e, i) => {
    s.addText(e, {
      x: rx + 0.3, y: 2.28 + i * 0.35, w: rw - 0.6, h: 0.32,
      fontFace: F.body, fontSize: 11, color: C.navy, margin: 0, valign: "middle",
      bullet: true,
    });
  });

  card(s, { x: rx, y: 4.12, w: rw, h: 2.1, fill: C.white });
  s.addText("既有可沿用", {
    x: rx + 0.3, y: 4.32, w: 3, h: 0.34, fontFace: F.head, fontSize: 17,
    bold: true, color: C.navy, margin: 0, valign: "middle",
  });
  const reuse = [
    "現有 Kubernetes 叢集（另建隔離命名空間）",
    "現有日誌與監控平台",
    "比對工具與腳本已完成並通過測試",
    "無須採購商用軟體授權",
  ];
  reuse.forEach((e, i) => {
    s.addText(e, {
      x: rx + 0.3, y: 4.74 + i * 0.35, w: rw - 0.6, h: 0.32,
      fontFace: F.body, fontSize: 11, color: C.gray, margin: 0, valign: "middle",
      bullet: true,
    });
  });

  footer(s, 8);
  s.addNotes("人月為概估，實際依端點數量調整。工具已完成，不列入開發工時。");
}

// =====================================================================
// 9  風險與緩解
// =====================================================================
{
  const s = pres.addSlide();
  pageTitle(s, "主要風險與緩解措施", "依影響程度排序");

  const risks = [
    ["極高", C.orange, "影子交易外洩至正式環境",
      "重複記帳、重複扣款、重複發送通知",
      "應用層演練模式 + 影子資料庫 + 網路層阻斷，三層皆須實證（W1 Gate）"],
    ["高", C.orange, "比對規則過度寬鬆，真實缺陷被掩蓋",
      "PoC 表面通過但實際未驗證到，最難事後發現",
      "忽略規則逐條簽核，金額與狀態欄位一律不得自動忽略；每階段人工抽樣複核 30 筆"],
    ["高", C.blue, "側錄的生產資料含個資",
      "法遵與個資保護違規",
      "法遵事前書面核准；離開正線主機前強制遮蔽；目錄加密並設定保留天數自動清除"],
    ["中", C.blue, "側錄程式佔用正線資源",
      "正線回應時間變慢",
      "壓測環境先行實測；取樣率由 5% 起；正線影響超過 2% 即不啟用"],
    ["中", C.teal, "誤判新系統性能不足",
      "錯誤結論導致不必要的重工",
      "以應用內部計時為準；資源規格對齊；先排除容器資源節流因素"],
  ];

  const heads = ["等級", "風險", "影響", "緩解措施"];
  const cx = [M, M + 1.0, M + 4.75, M + 7.9];
  const cwd = [1.0, 3.75, 3.15, CW - 7.9];

  heads.forEach((h, i) => {
    s.addShape(pres.ShapeType.rect, {
      x: cx[i], y: 1.62, w: cwd[i], h: 0.44,
      fill: { color: C.navy }, line: { color: C.navy, width: 1 },
    });
    s.addText(h, {
      x: cx[i] + 0.14, y: 1.62, w: cwd[i] - 0.28, h: 0.44,
      fontFace: F.body, fontSize: 11.5, bold: true, color: C.white,
      valign: "middle", margin: 0,
    });
  });

  const rh = 0.85;
  risks.forEach((r, i) => {
    const y = 2.06 + i * rh;
    for (let ci = 0; ci < 4; ci++) {
      s.addShape(pres.ShapeType.rect, {
        x: cx[ci], y, w: cwd[ci], h: rh,
        fill: { color: i % 2 === 0 ? C.white : C.cardGray },
        line: { color: C.line, width: 1 },
      });
    }
    s.addShape(pres.ShapeType.roundRect, {
      x: cx[0] + 0.16, y: y + 0.3, w: 0.68, h: 0.28, rectRadius: 0.03,
      fill: { color: r[1] }, line: { color: r[1], width: 0 },
    });
    s.addText(r[0], {
      x: cx[0] + 0.16, y: y + 0.3, w: 0.68, h: 0.28, fontFace: F.body, fontSize: 9.5,
      bold: true, color: C.white, align: "center", valign: "middle", margin: 0,
    });
    s.addText(r[2], {
      x: cx[1] + 0.14, y, w: cwd[1] - 0.28, h: rh, fontFace: F.body, fontSize: 11.5,
      bold: true, color: C.ink, valign: "middle", margin: 0, lineSpacingMultiple: 1.1,
    });
    s.addText(r[3], {
      x: cx[2] + 0.14, y, w: cwd[2] - 0.28, h: rh, fontFace: F.body, fontSize: 10.5,
      color: C.gray, valign: "middle", margin: 0, lineSpacingMultiple: 1.15,
    });
    s.addText(r[4], {
      x: cx[3] + 0.14, y, w: cwd[3] - 0.28, h: rh, fontFace: F.body, fontSize: 10.5,
      color: C.gray, valign: "middle", margin: 0, lineSpacingMultiple: 1.15,
    });
  });

  s.addText(
    "完整風險清單共 8 項，含回退機制與責任分工，詳見 PoC 規劃書第 10 節。",
    { x: M, y: 6.45, w: CW, h: 0.28, fontFace: F.body, fontSize: 10,
      italic: true, color: C.grayLt, margin: 0 }
  );

  footer(s, 9);
  s.addNotes("第二項風險最容易被輕忽 —— 它的失敗模式是「看起來成功」。");
}

// =====================================================================
// 10  決策請求
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.dark };

  s.addText("需要決策的四件事", {
    x: M, y: 0.62, w: CW, h: 0.6, fontFace: F.head, fontSize: 30,
    bold: true, color: C.white, margin: 0, valign: "middle",
  });
  s.addText("以下事項核准後即可啟動第一週作業", {
    x: M, y: 1.24, w: CW, h: 0.34, fontFace: F.body, fontSize: 13,
    color: C.blueLt2, margin: 0, valign: "middle",
  });

  const asks = [
    { n: "1", t: "核准 PoC 啟動", c: C.blue,
      b: "六週期程、3.2 人月投入、隔離環境建置。任一 Gate 未過將延長，屆時另行提報。" },
    { n: "2", t: "核准法遵送審", c: C.teal,
      b: "側錄生產流量涉及個資，需法遵書面核准資料側錄、遮蔽方式與保留天數。此為第一週的阻擋事項。" },
    { n: "3", t: "授權 Gate 停止機制", c: C.orange,
      b: "確認「Gate 未達標即停止推進」的權責歸屬，並確認不因時程壓力放行。核心交易階段要求 100% 一致。" },
    { n: "4", t: "確認驗收標準", c: C.blueLt2,
      b: "確認本 PoC 的產出是「已驗證範圍內的一致性證據」，切換上線決策另需納入營運、法遵與災備演練。" },
  ];

  const cw = (CW - 0.4) / 2;
  asks.forEach((a, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * (cw + 0.4);
    const y = 1.85 + row * 2.15;
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: cw, h: 1.9, rectRadius: 0.05,
      fill: { color: "0E2841" }, line: { color: "17395C", width: 1 },
    });
    numCircle(s, { x: x + 0.3, y: y + 0.3, n: a.n, color: a.c });
    s.addText(a.t, {
      x: x + 0.88, y: y + 0.3, w: cw - 1.15, h: 0.42, fontFace: F.head, fontSize: 17,
      bold: true, color: C.white, margin: 0, valign: "middle",
    });
    s.addText(a.b, {
      x: x + 0.3, y: y + 0.86, w: cw - 0.6, h: 0.85, fontFace: F.body, fontSize: 11.5,
      color: C.blueLt2, margin: 0, lineSpacingMultiple: 1.25,
    });
  });

  s.addText(
    "後續文件：PoC 規劃書、32 項工作檢核表、7 份架構決策紀錄（ADR）、可執行腳本與比對工具（已完成並通過測試）",
    { x: M, y: 6.35, w: CW, h: 0.4, fontFace: F.body, fontSize: 10.5,
      color: C.grayLt, margin: 0, valign: "middle" }
  );

  s.addNotes(
    "第 3 項是最需要當場確認的 —— 沒有這個授權，Gate 機制只是紙上作業。\n" +
    "第 4 項用來管理期待，避免 PoC 被當成上線許可。"
  );
}

pres.writeFile({ fileName: "平行測試PoC-管理層簡報.pptx" })
  .then(() => console.log("deck written"));
