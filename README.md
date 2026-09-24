# 岗位每日监测

每天自动检查几个招聘官网，**只要出现新岗位就给你发一封邮件**，里面包含岗位链接、岗位职责和任职要求。已经通知过的岗位不会重复打扰你；官网没变化的日子，一封邮件都不会收到。

当前监测这些来源（在 [`watchlist.json`](watchlist.json) 里配置）：

| 来源 | 范围 | 翻译 | 收件人变量 |
| --- | --- | --- | --- |
| Apple | [中国大陆 · Apple Pay 产品线](https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY) | 英文原文自动译成中文 | `MAIL_TO` |
| 阿里国际 AIDC | [社招 · 产品类 + 数据类](https://aidc-jobs.alibaba.com/off-campus/position-list?lang=zh) | 原文即中文，不翻译 | `MAIL_TO_CN` |
| 字节跳动 / TikTok | [社招 · 产品类目](https://jobs.bytedance.com/experienced/position) | 原文即中文，不翻译 | `MAIL_TO_CN` |
| 字节跳动 · 火山引擎 | 按业务线关键词筛，只要产品 / 解决方案向标题 | 同上 | `MAIL_TO_CN` |
| 字节跳动 · 红果短剧 | 按业务线关键词筛，只要产品 / 策略向标题 | 同上 | `MAIL_TO_CN` |
| 小红书 | [社招 · 产品 / 商家 / 电商 / 供应链 / 履约 / 交易 / 买手](https://job.xiaohongshu.com/social/position) | 同上 | `MAIL_TO_CN` |
| 同花顺 | [社招 · 全部岗位](https://campus.10jqka.com.cn/jobSocial/list?type=social)（当前仅 6 个，以算法/研究岗为主） | 同上 | `MAIL_TO_CN` |

每个来源单独发一封邮件、单独维护快照，可以发给不同的人。

同属字节的几个来源用的是同一个 `provider: bytedance`，区别只在 `keyword`（业务线）与 `title_pattern`（标题初筛），不需要额外写代码。

## 数据是怎么拿到的

每个站点各有各的脾气，`src/jobwatch/sources/` 下每个文件都写清了踩过的坑：

- **Apple** 是服务端渲染的，页面里内嵌了 `window.__staticRouterHydrationData`，搜索结果和岗位详情的结构化数据都在里面。直接读这段 JSON，拿到的是官网原文字段，而不是从 HTML 里猜出来的文本。
- **阿里国际** 的 `POST /position/search` 有 Spring Security 的 CSRF 保护：任意一次请求都会下发 `XSRF-TOKEN` cookie，之后把它作为 `_csrf` 查询参数带回去即可，不需要登录。另外类目筛选走的是 `subCategories`（子类 code），传顶级类 code 根本不生效，所以要先拉一次类目树把「产品类」展开成它的 4 个子类。
- **字节跳动** 的 `POST /api/v1/search/job/posts` 必须带 `website-path: society`（社招），填错会返回 `site not exist`。它在请求密集时会用 **405 Method Not Allowed** 来限流——不是真的不支持 POST，隔几秒重发就会成功，所以翻页之间有停顿，并且把 405 纳入了重试。同一条接口的 `keyword` 能按业务线筛（如「火山引擎」「红果」），因为这类岗位的标题尾巴上就带着业务线名。
- **小红书** 的页面是纯前端渲染的，直接 GET `/social/position` 只拿到几 KB 空壳，所以走 `POST /websiterecruit/position/pageQueryPosition`。这个接口的 `pageSize` 能到 100，一次就把岗位职责（`duty`）和任职要求（`qualification`）都带回来。注意它的多城市字段是全角逗号拼串且逗号后带空格（`"北京市， 上海市，深圳市"`），要拆开再 strip。
- **拼多多** 没做进去：`careers.pddglobalhr.com` 的岗位接口要 `anti_content` 反爬令牌（不带就返回 `errorCode 400023`），令牌由页面上的 JS 现场生成；它的 Next.js 数据路由和详情页也都不含岗位数据。要抓它得引入浏览器渲染，会让项目不再是零依赖。
- **同花顺** 的 `GET /api/v3/school_recruitment/apply/apply_list?type=social` 也不需要登录，一次返回岗位职责（`intro`）与任职要求（`requirement`）。但它**没有单岗位详情路由**（列表里点开是浮层，`/jobSocial/detail?id=x` 是空白页），所以卡片链接统一给列表页，另附职位编号。

阿里、字节、小红书的列表接口直接返回了岗位描述和任职要求，不用再抓详情页；只有 Apple 需要逐个岗位取详情。

整个流程：

```
逐个来源抓列表 → 和本地快照比对出新增 → 取正文 → 按需翻译 → 渲染邮件 → SMTP 发送 → 更新快照
```

邮件发送成功之后才更新快照。万一某天发信失败，这些岗位下次还会被当成"新增"重发，不会被漏掉。某个来源抓取失败也不影响其它来源。

## 快速开始

需要 Python 3.10+，**没有任何第三方依赖**，clone 下来就能跑。

```bash
# 看看各来源现在有哪些岗位（不发邮件、不写状态）
PYTHONPATH=src python3 -m jobwatch list --source alibaba-aidc

# 生成一封邮件预览，用浏览器打开 out/ 里的 .html 看效果
PYTHONPATH=src python3 -m jobwatch preview --source alibaba-aidc
```

配置发信：

```bash
cp .env.example .env
# 编辑 .env，填 MAIL_TO / MAIL_TO_CN / SMTP_USERNAME / SMTP_PASSWORD
PYTHONPATH=src python3 -m jobwatch mailtest --send   # 先验证发信通道
```

然后建立基线，之后每天跑一次：

```bash
PYTHONPATH=src python3 -m jobwatch seed   # 把现有岗位记为"已知"
PYTHONPATH=src python3 -m jobwatch run    # 之后每天执行这一条
```

> 某个来源如果还没建立过基线，首次运行会自动把当前岗位记为基线且**不发邮件**——否则你第一次就会收到一封列着上千个存量岗位的邮件。想收到这封"存量岗位汇总"，在 `.env` 里设 `NOTIFY_ON_FIRST_RUN=true`。

## 配置发信邮箱

### Gmail

Gmail 不接受账号密码直连，必须用**应用专用密码**。而应用专用密码又要求账号**先开启两步验证**，顺序不能反：

1. 先开启[两步验证](https://myaccount.google.com/signinoptions/twosv)，确认状态显示为「已开启」
2. 再进入[应用专用密码](https://myaccount.google.com/apppasswords)，创建一个，名字随便填
3. 把生成的 16 位密码（去掉空格）填进 `.env` 的 `SMTP_PASSWORD`

> 如果第 2 步提示 **「您的账号不支持您正在尝试的设置」**，说明第 1 步还没真正生效，详见下面的[常见问题](#常见问题)。

### QQ / 163 邮箱

在邮箱设置里开启 SMTP 服务并获取「授权码」，然后：

```env
SMTP_HOST=smtp.qq.com      # 163 用 smtp.163.com
SMTP_PORT=465
SMTP_SSL=true
SMTP_STARTTLS=false
SMTP_USERNAME=你的QQ号@qq.com
SMTP_PASSWORD=授权码
```

**没配 SMTP 也能用**：程序会自动降级，把邮件写到 `out/` 目录（`.html` / `.txt` / `.eml` 三种格式），你可以先看效果再决定要不要配。

## 翻译

只有 `translate: true` 的来源才会走翻译（目前只有 Apple，因为阿里和字节本来就是中文）。默认 `TRANSLATOR=auto`，按这个顺序找可用后端，某个挂了自动切下一个：

| 后端 | 需要 key | 说明 |
| --- | --- | --- |
| `llm` | `LLM_API_KEY` | 任意 OpenAI 兼容接口，翻译质量最好，推荐 |
| `deepl` | `DEEPL_API_KEY` | DeepL 免费额度够用（key 以 `:fx` 结尾会自动走 free 域名）|
| `google` | 不需要 | Google 翻译网页接口，**默认就能用**，可能被限流 |
| `mymemory` | 不需要 | 免费兜底，每日额度很小 |
| `none` | — | 全都不可用时保留英文原文，并在邮件里说明 |

译文缓存在 `.cache/translations.json`，同一段文字不会重复花钱或重复请求。

## 每天自动运行

### 方式一：GitHub Actions（推荐，不需要自己的电脑一直开着）

仓库里带了 [`.github/workflows/daily-watch.yml`](.github/workflows/daily-watch.yml)，每天北京时间 09:00 跑一次。在仓库 **Settings → Secrets and variables → Actions** 配置：

| 位置 | 名称 | 值 |
| --- | --- | --- |
| Secrets | `SMTP_USERNAME` | 发件邮箱地址 |
| Secrets | `SMTP_PASSWORD` | 应用专用密码 / 授权码 |
| Variables | `MAIL_TO` | Apple 岗位发给谁 |
| Variables | `MAIL_TO_CN` | 阿里 / 字节岗位发给谁 |

填在 Secrets 里也能跑（workflow 两边都会读），不用纠结放哪。但**必须填**——workflow 用的是 `run --require-mail`，配置不全会直接让这次运行失败并在日志里说明缺什么，而不是悄悄把邮件写成文件让你以为一切正常。

配好后去 Actions 页面手动跑一次 `workflow_dispatch`，勾上 `force` 可以强制发一封测试邮件；`source` 填某个来源 id 可以只跑那一个。

岗位快照 `state/*.json` 会由 workflow 自动提交回仓库，作为下次比对的依据。这个文件每天都会更新（哪怕岗位没变，`last_run` 时间戳也会变），所以仓库每天都有一次提交——这是刻意的：**GitHub 会在公开仓库连续 60 天没有活动后自动停用定时 workflow**，每日提交正好让它一直保持活跃。

### 方式二：本机 crontab（macOS / Linux）

```bash
./scripts/install_cron.sh        # 默认每天 09:00
./scripts/install_cron.sh 20 30  # 或指定 20:30
```

日志写在 `logs/watch-YYYY-MM.log`。

### 方式三：常驻进程

```bash
PYTHONPATH=src python3 -m jobwatch loop --interval 86400
```

## 命令一览

所有命令都支持 `--source <id>` 只处理某一个来源（可重复），不加就是全部。

| 命令 | 作用 |
| --- | --- |
| `run` | 执行一次监测，有新岗位就发邮件（定时任务调用的就是它）|
| `run --dry-run` | 不发邮件，只把邮件写到 `out/` |
| `run --force` | 即使没有新增岗位也发一封（测试用）|
| `run --require-mail` | 发信没配好就直接报错退出，而不是降级成本地预览（定时任务用）|
| `list` | 列出各来源当前命中的岗位，加 `--json` 输出结构化数据 |
| `preview` | 用真实岗位渲染一封邮件预览，加 `--send` 真的发出去 |
| `mailtest` | 验证发件邮箱能否登录，加 `--send` 发一封测试邮件 |
| `status` | 查看监测清单、本地快照和发信配置 |
| `seed` | 把当前岗位记为基线，不发邮件 |
| `top10` | 查看「最匹配你的 Top N」名单；加 `--rebuild` 重新抓全部来源重排 |
| `loop` | 常驻进程，按间隔重复检查 |

## 邮件末尾的 Top 10（本地口径）

每封通知邮件的末尾会附一份**跨来源**的「最匹配你的 Top N」名单，版式与上面的岗位卡一致，
只放摘要（名次、城市、年限门槛、命中词、链接）。名单的排序口径来自本地的
`local/profile.json`，它由自己的「业务地图 / 未来成长」那类文档提炼而来。

**这份口径刻意不进仓库。** `local/` 已在 `.gitignore` 里，所以：

- 名单与口径只存在你跑命令的这台机器上，不会被 GitHub Actions 的每日提交带上去；
- 换成在 GitHub Actions 上跑时，拿不到口径文件，末尾那一段自动略过（邮件照常发）；
- 想拿到云端就把口径本身传上去，那等于把业务背景写进仓库设置——默认不这么做。

### 名单怎么更新

- 每轮监测里新出现的岗位都会打分，与现有名单逐条比对：
  名额没满就补位；名额满了，新岗位要高出末位 `min_gain` 分才会替掉它（避免 1 分之差天天换人）；
  已下架的岗位直接出榜；表格里的「门槛超配」岗位只在名额空着时补位。
- 有变化时邮件主题会多一段「· Top10 有更新」，正文里也会写明本次新进与掉出。

### 一天只附一封

名单**不是每封邮件都附**：当天第一封发出去的邮件带上它，同一天后续的邮件不再重复附。
判定依据是 `local/top10.json` 里的 `top10_attached_on`（本地日期），而且**只有邮件真的发出去
之后**才会写入——发信失败时会顺延到当天下一次，不会被默默吃掉。

当天没有新岗位时不会为了名单单独发信：名单始终搭在「有新岗位」那封邮件上。

### 每个月提醒更新业务地图

口径来自业务地图，所以文档变了口径也得跟着变。距上次提醒超过 `business_map.interval_days`
（默认 30 天）时，邮件末尾会多一个提醒块，告诉你要过哪几件事、文档在哪、
更新完该跑哪条命令。提醒时间只在邮件真的发出去之后才推进。

```bash
PYTHONPATH=src python3 -m jobwatch top10              # 看当前名单
PYTHONPATH=src python3 -m jobwatch top10 --rebuild     # 口径改了，按新口径重排
PYTHONPATH=src python3 -m jobwatch preview --with-reminder   # 预览带 Top10 与提醒的版式
```

人工补充某个岗位的「对口 / 卡点」一句话说明，写进 `local/profile.json` 的 `notes`（键是岗位 id）即可。

## 改监测范围 / 加新来源

编辑 [`watchlist.json`](watchlist.json)，每条配置长这样：

```jsonc
{
  "id": "alibaba-aidc",              // 快照文件名，改了会丢历史
  "name": "阿里国际 AIDC · 产品类 / 数据类",  // 邮件标题里显示的名字
  "provider": "alibaba",             // apple / alibaba / bytedance
  "translate": false,                // 是否需要译成中文
  "recipients_env": "MAIL_TO_CN",    // 从哪个环境变量读收件人
  "enabled": true,
  "params": { "categories": ["97", "143"] }
}
```

`params` 因 provider 而异：

- **apple**：`list_url` 直接粘 [jobs.apple.com](https://jobs.apple.com/zh-cn/search) 上勾好筛选后的地址栏 URL，翻页和筛选都跟着它走。
- **alibaba**：`categories` 填顶级类目 code 或名字（`97`=产品类、`143`=数据类、`130`=技术类、`103`=运营类……），程序会自动展开成子类。也支持 `keyword`。
- **bytedance**：`category_ids` 填品类 id（从招聘页 URL 的 `category=` 参数里抄），另有 `keyword`、`location_codes`、`title_pattern`。

### 字节的岗位量与 `title_pattern`

字节那 4 个产品类目下有近 **1900 个在招岗位**，但那是存量——最近 30 天只新发布了约 100 个，**平均每天 3 个左右**，邮件量完全正常（建立基线时不会把存量发给你）。

如果只想要电商 / TikTok 相关的（约占三分之一），在 `params` 里填上正则即可：

```json
"title_pattern": "电商|TikTok|商城|商家|跨境"
```

## 常见问题

### 创建 Gmail 应用专用密码时提示「您的账号不支持您正在尝试的设置」

**最常见的原因是两步验证没开**——应用专用密码只对已开启两步验证的账号开放。先去[开启两步验证](https://myaccount.google.com/signinoptions/twosv)，再回到[应用专用密码](https://myaccount.google.com/apppasswords)页面。

如果两步验证确实开好了还是这个提示，那就是下面三种情况之一，这些账号**拿不到应用专用密码**，需要换一个发件邮箱：

- 两步验证只绑定了实体安全密钥（没有手机号或验证器 App）
- 账号加入了 [Google 高级保护计划](https://support.google.com/accounts/answer/7539956?hl=zh-Hans)，该计划明确禁用应用专用密码
- 这是公司 / 学校的 Google Workspace 账号，管理员禁用了应用专用密码

**换发件邮箱不影响收件**：发件用哪个邮箱都行，收件人保持不变即可。

### 收不到邮件，但程序显示发送成功

先去垃圾邮件里找一下。用 QQ / 163 往 Gmail 发信时，首封容易被归到「垃圾邮件」或「推广」标签，标记一次「非垃圾邮件」之后就正常了。

### 日志里出现「第 N 次请求失败…HTTP 405」

字节招聘站的限流表现，程序会自动退避重试，通常几秒后就成功。完整跑完字节那个来源大约需要 2~3 分钟（近 1900 个岗位、38 页）。

### 日志里出现「翻译后端 google 失败」

免费的 Google 网页翻译接口对同一个 IP 有频率限制，尤其在云服务器 / GitHub Actions 上。程序会自动降级到下一个后端，最差情况是保留英文原文并在邮件里说明，不会中断监测。想彻底稳定就配一个 `LLM_API_KEY` 或 `DEEPL_API_KEY`。

### 某个来源报错说站点改版了

解析不出来就报错退出，而不是静默发一封空邮件。`tests/fixtures/` 里存着各站点的真实响应快照，跑 `python3 -m pytest` 能定位是哪一层解析失效了。

## 开发

```bash
python3 -m pytest
```

```
src/jobwatch/
├── models.py        通用岗位模型（章节由来源自己决定）
├── sources/
│   ├── base.py      来源接口：列岗位 + 取正文
│   ├── apple.py     jobs.apple.com
│   ├── alibaba.py   aidc-jobs.alibaba.com（CSRF 握手 + 类目展开）
│   ├── bytedance.py jobs.bytedance.com（website-path + 限流重试）
│   ├── xiaohongshu.py job.xiaohongshu.com（纯前端站，走接口不抓 HTML）
│   └── tonghuashun.py campus.10jqka.com.cn（GET 接口，无单岗位路由）
├── config.py        watchlist.json + 环境变量
├── store.py         岗位快照，判断哪些是新增
├── match.py         匹配口径打分 + Top10 名单（口径读 local/profile.json）
├── translate.py     多后端翻译 + 磁盘缓存 + 自动降级
├── email_render.py  渲染 HTML / 纯文本邮件
├── mailer.py        SMTP 发送，未配置时落盘预览
├── watcher.py       串起整条流程
└── cli.py           命令行入口
```

## 说明

- 岗位信息与要求均来自各招聘官网原文，Apple 的中文为机器翻译，**以官网原文为准**。
- 程序只做只读访问，每天几次请求，对官网没有压力。
