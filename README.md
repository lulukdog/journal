# journal

一个私人日记。你随时说一句，它安静地收着；攒够了，它会在有话说的时候回来找你。

不是「每晚写日记」那种应用。触发场景是碎片化的——工位上、地铁上、吃饭时——所以入口是一步：打开就说话。

## 现在跑在哪

阿里云 ECS，`ssh journal-server`（地址见 `docs/superpowers/specs/2026-10-13-daily-journal-design.md` 第 3 节）。

手机上用 Android 应用 [Diary](https://github.com/lulukdog/diary-android)。浏览器打开 `http://<服务器地址>:10002/` 只会看到一页停用说明，不能再当日记用，也不要「添加到主屏幕」。

服务仍听在 `10002`。`/api/*` 和 `/media/*` 留给 Diary；网页端的 `index.html` + `app.js` 已经撤掉。

## 怎么用

在 Diary 里：

- **记一笔**——只记录，它默认不回应
- **问它**——明确提问，它必须回答
- 可以附一张图
- 可以切到前一天 / 后一天

第一次打开会先走六道题的访谈。那是它的第一批素材——**答完之前它没有资格主动开口**，因为「落差」「悬置」这类判断都需要基线。任何一题都可以跳过。

## 它什么时候开口

默认闭嘴。三个触发源：

1. 你明确问它 → 必须回答
2. 每天一张「今天的你」→ 这是承诺，不算开口，所以打开 App 永远不会是空的
3. 它自己判断有必要 → 例外

第三条建立在**可执行、可审计的规则**上，不是「让模型自己觉得」：

| 信号 | 判据 |
|---|---|
| repeat | 同一情绪词在 3 天里被 N 条记录提到 |
| drift | 最近 7 天的状态和之前 30 天偏离超过阈值 |
| dangling | 提过一个决定，7 天以上没有下文 |
| upcoming | 最近提到过时间点（第一版没推送，这条基本没用） |
| silence | 连续 3 天没说话（有上限和熔断） |
| risk | 明确的极端表达 |
| checkin | 当天的话里听得出累、压力、难过、孤单、紧张或高兴 |

每条触发都写进 `events` 表 —— 事后能查「它为什么在那天找我」。

### 情绪上它怎么陪

默认仍然闭嘴。记一笔不会弹出一段分析。

听得出语气时，它只做两件很短的事：

- **日卡片**上留一句陪伴（`card.companion`，白天还没收口时先放在 `mood.line`）。累了就接住，高兴了就跟着高兴一下。不建议，不上课。
- **收口**时若 `checkin` 成立，再问一句轻的（`utterances`，`trigger` 为 `signal:checkin`）。先问，再判断。同一天不重复说。若同时有 `risk`，只留那句更重的陪伴，不再叠一句日常问候。

`card.mood`（-2 到 2）仍是天气条，**不是**抑郁评分。接口里的 `mood` 对象也没有分数字段。极端表达只回应「我在，去找一个你信得过的人」，不做诊断。

**它开口时先问，再判断。** 它可以说「你这几天提了四次累」，不会说「你状态不好」。猜错了，问一句的代价只是一句多余的关心。

**它不做心理诊断**，不给抑郁评分，不推送「你可能有抑郁倾向」。这类判断应该交给人。

## 结构

```
app/
  main.py        FastAPI 路由
  db.py          SQLite 建表
  config.py      .env 读取
  auth.py        口令换 cookie
  days.py        一天的边界（凌晨 4:00）+ 每日卡片
  entries.py     记一笔
  memory.py      记忆分层与上下文组装
  llm.py         零一万物 / OpenAI 兼容客户端
  prompts.py     系统提示词
  companion.py   听语气、写一句短陪伴（无评分）
  signals.py     信号（含当天的 checkin）
  dayclose.py    每晚收口
  onboarding.py  引导访谈
  static/        网页停用说明（不再是可交互的日记前端）
tests/           pytest（含陪伴与 Bearer）
deploy/          systemd unit + 部署脚本
```

记忆分四层：原始记录 → 日卡片 → 周/月总结 → 画像（`memories`）。**画像和流水是分开的**——你问「我该不该辞职」，需要的是「他的收入结构是什么、有什么负担」这种有限条数、会被更新的事实，不是一堆原始句子。

## 口令

口令是进这个应用唯一的一道门，存在服务器上的 `/opt/journal/.env`。

```bash
# 忘了就查出来
ssh journal-server 'python3 /opt/journal/deploy/token.py show'

# 换一个随机的
ssh journal-server 'python3 /opt/journal/deploy/token.py new'

# 或者设成你记得住的
ssh journal-server 'python3 /opt/journal/deploy/token.py set 我的口令'
```

### 平时不用输

Diary 把口令存在应用里，之后每个请求带 `Authorization: Bearer`。网页已经停用，没有「添加到主屏幕」这一路了。

`POST /api/login` 仍会换一年的 HttpOnly cookie，给还在用 cookie 的调用方。改了服务器上的口令之后，旧 cookie 和旧 Bearer 都会失效，需要重新登录。

### 但是如果口令很短

六位数字这种口令在公网上是可以被穷举的。真正的防线不是口令复杂度，是**登录限流**：

- 同一个 IP 错 5 次 → 锁 15 分钟
- 锁定期内就算口令对了也不放行
- 锁按 IP 隔离，别人被锁不影响你

所以弱口令在这个限流下是可接受的（每天最多试 480 次，六位数字要跑好几年）。
但如果你把限流关了，它会在几分钟内被打开。

改口令会重启服务，**所有设备需要重新登录一次**。因为 cookie 里存的就是口令本身——
没有用户表、没有数据库，简单到不会出错。

### Android 用同一口令

日常客户端是 [diary-android](https://github.com/lulukdog/diary-android)。仍是这一个 `JOURNAL_APP_TOKEN`，不要另开账号。网页已停用；App 每个请求带：

```http
Authorization: Bearer <JOURNAL_APP_TOKEN>
```

明文 HTTP 是已知取舍，不要为了 Android 去强制 HTTPS。服务根地址形如 `http://<服务器>:10002`。

| 用途 | 方法 | 路径 |
|---|---|---|
| 这天的卡片、记录、它说过的话、心情回声 | GET | `/api/state?day=YYYY-MM-DD` |
| 记一笔（只落库，不立刻说话） | POST | `/api/entries` |
| 明确问它（必须回答，情绪先接住） | POST | `/api/ask` |
| 图片 | GET | `/media/<image_path>` |
| 访谈 | GET/POST | `/api/onboarding`、`/api/onboarding/answer` |
| 口令是否有效 | GET | `/api/me` |

一天的边界是 **04:00 Asia/Shanghai**。`/api/state` 里 Android 可以直接画：

- `card.companion`：收口后的陪伴句。`card.mood` / `mood_label` / `headline` / `keywords` 仍是原来的卡片。
- `mood`：`{tone, line, heard, source}`。`line` 为空就什么都别显示。`tone` 只是 `tired` / `stressed` / `sad` / `lonely` / `anxious` / `happy` / `heavy` / `null`，`heavy` 表示出现了极端表达。没有评分。
- `utterances[].content`：它开口说的话。`trigger` 为 `ask`（回答）、`signal:checkin`（轻问一句）或其它 `signal:*`。
- `entries[].media_url`：有图时是 `/media/...`，和 `image_path` 一起给。

`POST /api/ask` 返回 `{reply, mood}`。`reply` 是完整回答；只想展示一句时用 `mood.line`。

## 常用命令

```bash
# 部署（本地跑）
bash deploy/deploy.sh

# 手动跑一次收口
ssh journal-server 'cd /opt/journal && sudo -u journal .venv/bin/python run_dayclose.py [YYYY-MM-DD]'

# 手动备一份
ssh journal-server 'cd /opt/journal && sudo -u journal .venv/bin/python run_backup.py'

# 测试
ssh journal-server 'cd /opt/journal && .venv/bin/python -m pytest tests/ -q'

# 看日志
ssh journal-server 'tail -50 /var/log/journal-app.log'
ssh journal-server 'tail -50 /var/log/journal-dayclose.log'
ssh journal-server 'tail -50 /var/log/journal-backup.log'
```

改配置：编辑服务器上的 `/opt/journal/.env`，然后 `systemctl restart journal`。

## 已知的取舍

这些都是想清楚了才留下的，不是没做完：

- **第一版没有推送。** 它想说的话存在那儿，等你下次打开时看到。代价是「临近」这条信号失效了，而且原来设想的「每天推送看心情」没有了——变成了一张你打开才看到的卡片。
- **没有 HTTPS。** 没有域名就没有证书，手机到服务器这一路是明文的。靠一个长随机口令兜着。要补的话，Tailscale 或域名 + 备案。
- **root 密码没改，SSH 22 对公网开着。** 这是个明确记录了风险、但用户决定保持现状的选择。
- **登录口令是用户自己选的短口令**（六位数字）。靠登录限流兜着，见上面的「口令」一节。
- **图片不限量。** 当天的照片全发，不多花心思挑。代价是某天你拍了几十张的话，请求会很大很慢，
  甚至可能超过模型服务的请求体上限。到那天再说。
- **检索用 LIKE 不用 FTS5。** FTS5 的 trigram 分词器最少要 3 个字符，而日记里大量是「累」「烦」这种一两个字，会搜不到。数据量几千条时 LIKE 是毫秒级。
- **数字守卫只在 repeat 和 silence 上生效。** 模型自己数数字几乎每次都会错，所以我们用代码兜底。但 dangling / upcoming 里的日期和引文还没有对应的守卫。
- **一次收口只处理一天。** 时区写死在 systemd unit 里（`TZ=Asia/Shanghai`），因为一天的边界是产品行为的一部分。

## 数据在哪

服务器上：

- `/var/lib/journal/journal.db` —— 全部记录（单文件 SQLite）
- `/var/lib/journal/media/` —— 原图，按 `年/月` 分目录
- `/var/lib/journal/backups/` —— 每晚 04:30 用 SQLite 的在线备份 API 备一份，保留 30 天

注意：备份和原库在同一块盘上，只能防误删，防不了磁盘挂。真要防的话得传到别的地方。

**每条记录都会发给模型服务商，而且照片也会。** 每日卡片和「问它」会把当天全部照片压缩到长边 1568px 后以 base64 一起发过去——
否则模型只知道「这里有一张图」，等于照片白拍了。这是为了判断质量而接受的取舍。

模型选择：

```bash
JOURNAL_MODEL=deepseek-flash       # DeepSeek-V4.1-Flash，能看图
JOURNAL_MODEL_DEEP=deepseek-flash  # 「问它」用哪个
```

换成 `deepseek-v4-pro` 会失去看图能力（它不支持图片输入，会直接报错）。
代码里**故意不做静默降级** —— 模型哪天开始读不懂你的照片，你永远发现不了。宁可报错。
