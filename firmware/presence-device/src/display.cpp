#include "display.h"

#include <U8g2lib.h>
#include <Wire.h>

#include <vector>

namespace {

// 硬件参数照抄 D:\ai\hardware\Emerald-hello\src\main.cpp：
// SSD1306 128x64，I2C，SDA=5 / SCL=6，地址 0x3C。
constexpr int kSdaPin = 5;
constexpr int kSclPin = 6;
constexpr int kScreenW = 128;
constexpr int kScreenH = 64;

// hello 未渲染过中文；u8g2_font_wqy12_t_gb2312 是 Part 3.4 决策落地的中文字库方案，
// 该字体自带 ASCII + GB2312 汉字字形，drawUTF8() 可直接吃 UTF-8 源串，无需手动转码。
#define CJK_FONT u8g2_font_wqy12_t_gb2312
constexpr int kLineHeight = 12;
constexpr int kMargin = 4;   // 四边统一页边距(px)，Part A：修右列截字
constexpr int kUsableW = kScreenW - kMargin * 2;
constexpr int kUsableH = kScreenH - kMargin * 2;
constexpr int kMaxLines = kUsableH / kLineHeight;   // 按可用高度算，避免加了边距还溢出

// 屏幕类型判定（Part 决策-3）：Emerald-hello 用的是 Adafruit_SSD1306 单色 OLED（非 RGB 驱动），
// 深蓝做不到，已退化为「实心点亮」——renderHeart() 直接用 drawDisc/drawTriangle 实心填充，
// 没有颜色可选，故本文件不需要 HEART_COLOR 宏或彩色分支。

// 分段自动翻页间隔；最后一段常驻直到下条消息。
constexpr unsigned long kSegmentHoldMs = 2500;
constexpr int kDefaultHeartDurationMs = 4000;

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/U8X8_PIN_NONE);

enum class Mode {
    OFFLINE,
    STREAMING,
    PAGED,   // segments / channel_message 分页展示
    HEART,
    STANDBY, // Part C：最后一条消息静置超过 kIdleMs 后的待机轮播
};

Mode mode = Mode::OFFLINE;
ConnState connState = ConnState::WIFI_CONNECTING;
bool everConnectedOnce = false;

String streamMsgId;
String streamBuffer;
unsigned long lastStreamRedraw = 0;
constexpr unsigned long kStreamRedrawThrottleMs = 120;

std::vector<String> pages;   // 每页已按屏宽换行好的多行文本，用 "\n" 分隔行
size_t pageIndex = 0;
unsigned long lastPageSwitch = 0;

unsigned long heartUntilMs = 0;

// ── Part C：30s 待机随机显示 ─────────────────────────────────────────────
constexpr unsigned long kIdleMs = 30000;         // 最后一条消息静置多久后进入待机
constexpr unsigned long kStandbyRotateMs = 9000; // 待机时每隔多久换一句
unsigned long lastActivityMs = 0;   // 每次有新消息渲染（含流式增量）时刷新
unsigned long lastStandbyRotate = 0;

// 死显示文案；C.3 智能化接口先留桩：将来可换成读后端下发缓存。
const char *const kStandbyLines[] = {
    "......",
    "在想你",
    "在等你",
    "发呆中",
};
constexpr int kStandbyLineCount = sizeof(kStandbyLines) / sizeof(kStandbyLines[0]);

// TODO(C.3)：现在随机返回 kStandbyLines；将来后端可通过 action 下发真实待机状态
// （如在读书/睡觉/等你），固件优先用下发内容，无下发时回退到这里。
const char *getStandbyLine() {
    return kStandbyLines[random(kStandbyLineCount)];
}

void markActivity() { lastActivityMs = millis(); }

// ── UTF-8 逐字符遍历 ──────────────────────────────────────────────────────
size_t utf8CharLen(uint8_t lead) {
    if ((lead & 0x80) == 0x00) return 1;
    if ((lead & 0xE0) == 0xC0) return 2;
    if ((lead & 0xF0) == 0xE0) return 3;
    if ((lead & 0xF8) == 0xF0) return 4;
    return 1;  // 非法字节，当单字节跳过，避免死循环
}

// 把一段文本按屏宽自动换行（不跨越已有的 "\n" 段边界，调用方按 \n 先切好段）。
std::vector<String> wrapLine(const String &text) {
    std::vector<String> lines;
    String cur;
    int curWidth = 0;
    size_t i = 0;
    size_t n = text.length();
    while (i < n) {
        size_t clen = utf8CharLen((uint8_t)text[i]);
        if (i + clen > n) clen = n - i;
        String ch = text.substring(i, i + clen);
        int w = u8g2.getUTF8Width(ch.c_str());
        if (curWidth + w > kUsableW && cur.length() > 0) {
            lines.push_back(cur);
            cur = "";
            curWidth = 0;
        }
        cur += ch;
        curWidth += w;
        i += clen;
    }
    lines.push_back(cur);  // 允许空行（空段落也占一行）
    return lines;
}

// 把「按 \n 分好的段」渲染成分页列表：每段先换行，再按 kMaxLines 切页；
// 段边界始终另起一页（一段一屏），段内溢出才继续往下翻页。
std::vector<String> buildPages(const String &content) {
    std::vector<String> result;
    int start = 0;
    int n = content.length();
    while (start <= n) {
        int nl = content.indexOf('\n', start);
        String segment = (nl == -1) ? content.substring(start) : content.substring(start, nl);
        std::vector<String> lines = wrapLine(segment);
        for (size_t i = 0; i < lines.size(); i += kMaxLines) {
            String page;
            for (size_t j = i; j < lines.size() && j < i + kMaxLines; j++) {
                if (j > i) page += "\n";
                page += lines[j];
            }
            result.push_back(page);
        }
        if (nl == -1) break;
        start = nl + 1;
    }
    if (result.empty()) result.push_back("");
    return result;
}

// 统一居中排版器（Part A）：整块垂直居中 + 每行水平居中，四边留 kMargin。
// 分段、流式、待机文案都走这一个函数，避免散着 drawUTF8 各算各的坐标。
void drawLinesCentered(const std::vector<String> &lines) {
    u8g2.clearBuffer();
    u8g2.setFont(CJK_FONT);
    int blockH = (int)lines.size() * kLineHeight;
    int yTop = kMargin + (blockH < kUsableH ? (kUsableH - blockH) / 2 : 0);
    for (size_t i = 0; i < lines.size(); i++) {
        int w = u8g2.getUTF8Width(lines[i].c_str());
        int x = kMargin + (w < kUsableW ? (kUsableW - w) / 2 : 0);
        int y = yTop + (int)(i + 1) * kLineHeight;
        u8g2.drawUTF8(x, y, lines[i].c_str());
    }
    u8g2.sendBuffer();
}

void renderPage(const String &page) {
    std::vector<String> lines;
    int start = 0;
    int n = page.length();
    while (start <= n && lines.size() < (size_t)kMaxLines) {
        int nl = page.indexOf('\n', start);
        lines.push_back(nl == -1 ? page.substring(start) : page.substring(start, nl));
        if (nl == -1) break;
        start = nl + 1;
    }
    drawLinesCentered(lines);
}

void renderStreamingTail() {
    // 流式：把已收到的增量按屏宽换行后，只显示能塞进屏幕的最后 kMaxLines 行（滚动手感）。
    std::vector<String> lines = wrapLine(streamBuffer);
    size_t startIdx = lines.size() > (size_t)kMaxLines ? lines.size() - kMaxLines : 0;
    if (startIdx > 0) {
        lines.erase(lines.begin(), lines.begin() + startIdx);
    }
    drawLinesCentered(lines);
}

void renderCentered(const String &text) {
    drawLinesCentered({text});
}

void renderOffline() {
    switch (connState) {
        case ConnState::WIFI_CONNECTING:
            renderCentered("连接WiFi中...");
            break;
        case ConnState::WS_CONNECTING:
            renderCentered("连接后端中...");
            break;
        default:
            renderCentered("离线，重连中...");
            break;
    }
}

// 实心大爱心：两个圆 + 一个倒三角拼成，占屏高约一半（单色屏用点亮色，无法做深蓝）。
void renderHeart() {
    u8g2.clearBuffer();
    int cx = kScreenW / 2;
    int cy = kScreenH / 2 - 2;
    int r = kScreenH / 4;  // 两圆半径，整体高度约等于屏高一半

    u8g2.drawDisc(cx - r, cy, r, U8G2_DRAW_ALL);
    u8g2.drawDisc(cx + r, cy, r, U8G2_DRAW_ALL);
    // 下方三角形补出爱心尖角。
    int leftX = cx - 2 * r, rightX = cx + 2 * r;
    int topY = cy;
    int tipY = cy + 2 * r + r / 2;
    u8g2.drawTriangle(leftX, topY, rightX, topY, cx, tipY);
    u8g2.sendBuffer();
}

void renderCurrentPage() {
    if (pages.empty()) return;
    renderPage(pages[pageIndex]);
}

void renderStandbyLine() {
    drawLinesCentered({String(getStandbyLine())});
}

// HEART 结束、或每次 tick 检查 PAGED 是否该转入待机时的落地状态。
// 只在已经展示过至少一条消息（pages 非空）时才允许进入待机。
void enterRestState() {
    if (!pages.empty() && millis() - lastActivityMs > kIdleMs) {
        mode = Mode::STANDBY;
        lastStandbyRotate = millis();
        renderStandbyLine();
    } else if (!pages.empty()) {
        mode = Mode::PAGED;
        renderCurrentPage();
    } else {
        mode = Mode::OFFLINE;
        renderCentered("已连接，等待消息...");
    }
}

}  // namespace

void displaySetup() {
    Wire.begin(kSdaPin, kSclPin);
    u8g2.setFont(CJK_FONT);
    u8g2.setFontMode(0);
    renderOffline();
}

void displaySetConnState(ConnState state) {
    connState = state;
    if (state == ConnState::ONLINE) {
        everConnectedOnce = true;
        if (mode == Mode::OFFLINE) {
            renderCentered("已连接，等待消息...");
        }
        return;
    }
    mode = Mode::OFFLINE;
    renderOffline();
}

void displayTick() {
    if (connState != ConnState::ONLINE) {
        return;  // renderOffline 已经在状态切换时画过，非阻塞、无需每帧重绘
    }
    if (mode == Mode::HEART) {
        if ((long)(millis() - heartUntilMs) >= 0) {
            // 爱心结束回到「上一条文字或待机」，不因爱心本身重置 30s 计时（C.2）。
            enterRestState();
        }
        return;
    }
    if (mode == Mode::STANDBY) {
        if (millis() - lastStandbyRotate >= kStandbyRotateMs) {
            lastStandbyRotate = millis();
            renderStandbyLine();
        }
        return;
    }
    if (mode == Mode::PAGED) {
        if (millis() - lastActivityMs > kIdleMs) {
            mode = Mode::STANDBY;
            lastStandbyRotate = millis();
            renderStandbyLine();
            return;
        }
        if (pages.size() > 1 && pageIndex < pages.size() - 1 &&
            millis() - lastPageSwitch >= kSegmentHoldMs) {
            pageIndex++;
            lastPageSwitch = millis();
            renderCurrentPage();
        }
    }
}

void displayStreamStart(const String &msgId) {
    mode = Mode::STREAMING;
    streamMsgId = msgId;
    streamBuffer = "";
    lastStreamRedraw = 0;
    markActivity();
    u8g2.clearBuffer();
    u8g2.sendBuffer();
}

void displayStreamDelta(const String &msgId, const String &delta) {
    if (mode != Mode::STREAMING || msgId != streamMsgId) return;
    streamBuffer += delta;
    markActivity();  // 长消息持续流入时不该中途被判定为「静置超时」
    unsigned long now = millis();
    if (now - lastStreamRedraw < kStreamRedrawThrottleMs) return;
    lastStreamRedraw = now;
    renderStreamingTail();
}

void displayStreamEnd(const String &msgId) {
    if (msgId != streamMsgId) return;
    renderStreamingTail();  // 冻结当前缓冲；随后的 message_segments 会覆盖为权威版
}

void displaySegments(const String &msgId, const String &content) {
    (void)msgId;
    markActivity();
    pages = buildPages(content);
    pageIndex = 0;
    lastPageSwitch = millis();
    mode = Mode::PAGED;
    renderCurrentPage();
}

void displayChannelMessage(const String &msgId, const String &content) {
    // 主动消息（无前置流式）：直接按 \n 分段展示，逻辑同 message_segments。
    displaySegments(msgId, content);
}

void displayShowHeart(int durationMs) {
    if (durationMs <= 0) durationMs = kDefaultHeartDurationMs;
    heartUntilMs = millis() + (unsigned long)durationMs;
    mode = Mode::HEART;
    renderHeart();
}
