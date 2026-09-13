# 求职报告运行手册

本手册对应 `scripts/run_job_match.py` 和 `scripts/jobmatch_runtime/`。命令从本仓库根目录执行。示例中的企业、岗位、经历、域名、模型名和服务编号都是说明用值；不代表真实岗位或已部署服务。执行前用经过授权的实际输入替换。

## 1. 先明确这套程序做什么

程序读取你提供的具体招聘页面列表，抓取公开正文，分别调用 SourceScout、EvidenceMapper、Auditor 三个模型角色，保留来源材料、阶段结果和检查点。资料完整性满足要求时，它生成 Word 机器分析草稿和人工复核表。人工读源核查后，`finalize` 才能把符合条件的岗位列入已确认主清单。

它不会替你投递、联系雇主、创建平台账号、上传简历到招聘网站，也不会自动安装全部第三方软件。`sources` 是显式输入列表，不是自动全网搜索。JobSpy 可以另行发现线索，但线索需要重新核查官方 JD，再加入来源列表。

12 个适配器是可选择的功能入口，不是每份报告必须依次运行的 12 道步骤。O*NET 和 ESCO 用于分类参考；文档解析用于提取文字；职业模型用于提出分类或改写建议；MELO 与 TGRE 用于评估已有预测。它们都不能替代中国招聘页面上的届别、城市、开放状态和资格条件。

注册表的 `default_enabled: false` 表示必须显式选择，不能据此判断代码是否存在。适配器配置只有 `enabled: true` 才允许执行。安装完成、接口可调用、真实服务调用成功、完整基准评估完成，是四种不同状态。

## 2. 安装基础运行环境

从简历文件开始，先读[输入准备流程](preparation-entry.md)，完成本地提取、经历确认、指定招聘入口的链接发现和来源确认；之后得到本手册所需的三个输入文件。新入口不替代搜索服务，也不自动确认在招状态。

需要排查上游条件时，使用[运行条件检查](deployment-checks.md)和 `python scripts/check_runtime.py`。默认只做被动检查；`--live` 只访问明确指定的只读健康路径，不执行模型任务，也不把HTTP响应当作模型可用。

建议 Python 3.10 或以上。基础流水线需要 Python 标准库和 `python-docx`；Word 依赖已固定在 `requirements.txt`。模型服务、浏览器、OCR 和第三方数据集不包含在这个安装步骤中。

本地 PDF 输入准备另需 `pypdf`，也固定在基础依赖中。文本和 Word 解析不使用 OCR；扫描件须另行处理后核对。

```bash
python3 -m venv ../jobmatch-venv
source ../jobmatch-venv/bin/activate
python -m pip install -r requirements.txt
python scripts/run_job_match.py --help
python scripts/run_job_match.py doctor
```

也可单独安装相同的 Word 依赖：

```bash
python -m pip install python-docx==1.2.0
```

Windows 使用对应虚拟环境的 `Scripts/activate`。下文命令统一写 `python`，指这个已安装依赖的解释器。

`doctor` 不抓取网页、不调用模型、不检查真实岗位。它只检查 SDK 是否可导入、配置字段或本地检出目录是否存在，并始终注明 `live_verified: false`。不要把 `configuration_present: true` 当作上游服务已经验收。

可选 SDK 使用 `scripts/install_optional.py`，只支持 Docling、Crawl4AI、JobSpy 三项。默认仅显示安装计划；加 `--execute` 才会在当前隔离虚拟环境中安装。执行前检查计划中上游地址和固定提交。安装助手不修改全局 Python，不下载模型或浏览器，不启动服务、不创建账号。

```bash
python scripts/install_optional.py docling
python scripts/install_optional.py docling --execute
```

安装后的 `installed_verified` 要求 pip 成功、PEP 610 元数据中的来源与提交吻合、`pip check` 通过；`installed_unverified` 不能当成已验收安装。传递依赖仍由 pip 解析，这不是完整依赖锁定。实际模型效果仍显示未评估。其他 9 项交给版本化数据读取或人工部署，安装助手会返回 `manual_provisioning_required`。

## 3. 把运行材料放在仓库之外

补充组件的 `adapter_steps` 可配置 `on_failure: "continue"`，默认仍是 `stop`。只有缺少依赖、模型/服务未就绪、网络错误、429和特定5xx等可用性故障允许继续；警告写入报告并关联岗位编号。隐私授权、SSRF、凭据、输出结构或原文引用问题仍停止该岗位处理。缺少补充工具不改变招聘状态和资格门槛。

建议目录关系：

```text
social-science-job-match-skill/  公开的代码仓库
jobmatch-venv/                  本地 Python 环境
jobmatch-private/               不提交、不公开的运行材料
  config.json
  candidate.json
  sources.json
  inputs/
  adapter-results/
  run-001/
```

创建私有目录后，再用编辑器保存下文 JSON：

```bash
mkdir -m 700 ../jobmatch-private
mkdir -m 700 ../jobmatch-private/inputs
mkdir -m 700 ../jobmatch-private/adapter-results
```

这些目录若已经存在，不要重新创建或清空，直接检查其用途和权限。运行目录 `run-001` 由程序创建；首次运行必须为空。`run` 拒绝把运行结果写入 Skill 仓库内部，但独立 `adapter`、`report`、`finalize` 的输出位置仍需由操作者正确选择。

密钥只通过环境变量读取。配置文件只写变量名，如 `JOB_MATCH_API_KEY`、`JOBMATCH_CLASSIFIER_KEY` 或 `JOBMATCH_SERVICE_TOKEN`。通过你的密钥管理器或终端安全方式设置实际值；不要把实际密钥写入 JSON、脚本、README 或 Git。不要把私有运行目录加入公开仓库。

## 4. 先确定招聘模式和经历记录

### 4.1 招聘模式

必须选择以下一个值，程序不根据“在读”自行推断实习：

| 值 | 使用场景 | Word 必须呈现的模式信息 |
| --- | --- | --- |
| `internship` | 实习 | `earliest_start`、`days_per_week`、`duration_months` |
| `campus_full_time` | 校招全职，包括秋招和春招 | `graduation_cohort`、`recruitment_batch`、`graduate_eligibility` |
| `experienced_full_time` | 社招全职 | `experience_requirement`、`earliest_start` |

同一报告只能包含一个模式。城市使用可直接比对的名称，如 `杭州`、`广州`、`深圳`，不要把“广深”作为单个城市。毕业时间、历史全职经历和应届资格是否符合某个雇主，仍需逐岗读取 JD；不能仅凭学位层级下结论。

### 4.2 `candidate.json`

输入是经历记录，不是把整份简历随意塞入一个文本字段。每条记录保留来源和位置；`confirmed` 必须来自候选人的确认或已完成的材料核查，不能由模型自行置为真。

以下是模拟输入结构。真实运行前逐项替换，不要用模拟经历生成真实求职报告：

```json
{
  "candidate_evidence": [
    {
      "id": "E1",
      "source_id": "resume-reviewed",
      "locator": "第 1 页，项目经历第 1 项",
      "text": "模拟材料：在课程项目中清洗问卷数据并撰写分析说明。",
      "state": "completed",
      "confirmed": true
    },
    {
      "id": "E2",
      "source_id": "candidate-confirmation",
      "locator": "求职目标确认记录第 1 项",
      "text": "模拟材料：预计 2027 年毕业，目标为校招全职，接受杭州、广州和深圳。",
      "state": "ongoing",
      "confirmed": true
    }
  ]
}
```

`state` 仅允许 `completed`、`ongoing`、`planned`、`unconfirmed`。岗位条件判为 `met` 或改写判为 `ready` 时，引用必须指向 `confirmed: true` 且状态为 `completed` 或 `ongoing` 的经历记录。没有提到某项技能，应记未知，不能自动判定不具备。

Docling 可帮助提取文本，但不能自动完成这一步事实确认。流水线目前直接接收经历记录，不直接接收 PDF 简历。

## 5. 准备来源列表和模型配置

### 5.1 `sources.json`

每行指向一个具体职位页面。下例是域名结构示意，不是可运行的真实岗位列表：

```json
[
  {
    "url": "https://careers.example.org/jobs/position-123",
    "source_tier": "employer_official",
    "company_key": "example-employer",
    "entity_type": "company"
  }
]
```

`url` 和 `source_tier` 必填；`company_key`、`entity_type` 可选。`company_key` 用于把同一家企业的不同写法归一，不能为同一企业人为制造多个计数。

来源等级允许 `employer_official`、`official_ats`、`employer_verified_platform`、`official_repost`、`aggregator`。只有前三类可能进入主清单。来源等级是需要人工核查的输入元数据，程序不证明该网站确实归雇主所有。

抓取要求 HTTPS、主机在允许列表中、同意该来源的采集条款，并能核查 robots.txt。403、429、robots 拒绝、无法核查 robots、动态页面正文不完整，都不能通过绕过机制伪装成成功。当前主流水线读取静态 HTML 或文本；没有自动浏览器登录或验证码处理。

### 5.2 `config.json`：本地模型服务示例

这个配置假设你已经部署一个支持 `/v1/chat/completions`、JSON 输出模式且确实不向外转发数据的本地模型服务。模型名需要换成该服务实际加载的模型。`service_egress` 是操作者的声明，不是程序完成了网络隔离验收。

```json
{
  "employment_mode": "campus_full_time",
  "locations": ["杭州", "广州", "深圳"],
  "target_companies": 20,
  "allowed_source_hosts": ["careers.example.org"],
  "source_terms_accepted": false,
  "source_cache_seconds": 3600,
  "max_source_chars": 100000,
  "refresh_sources": false,
  "limits": {
    "max_jobs": 60,
    "max_parallel_calls": 2
  },
  "privacy": {
    "allow_remote_candidate_data": false,
    "cache_candidate_data": false
  },
  "llm": {
    "provider": "openai_compatible",
    "base_url": "http://127.0.0.1:8000/v1",
    "model": "YOUR_INSTALLED_MODEL",
    "service_egress": "local_only",
    "max_attempts": 2,
    "max_output_tokens": 5000,
    "timeout_seconds": 90,
    "roles": {}
  },
  "adapters": {},
  "adapter_steps": []
}
```

先确认采集条款与授权来源，再把 `source_terms_accepted` 改为 `true`。不要把示例域名或未部署模型直接用于正式运行。

`max_jobs` 为 1–300，约束输入来源数量；`max_parallel_calls` 为 1–4，实际控制同时处理的岗位任务数量。一个岗位内仍按 Scout → Mapper → Auditor 顺序调用；不同岗位可以并行。每个模型调用的 `max_attempts` 为 1–3。程序记录实际调用次数和耗时，但不提供精确的 token 总预算或费用结算器。

远程 OpenAI-compatible 服务使用真实 HTTPS `base_url`、模型名和 `api_key_env`。私有经历记录会进入 Mapper 与 Auditor 的请求，只有获得相应授权后才能设置 `privacy.allow_remote_candidate_data: true`。这个总开关不代替每个第三方适配器自己的隐私开关。

### 5.3 三个独立角色

三个角色使用不同指令和独立请求；不是一次模型回答贴三个标签。它们可以使用同一模型，也可通过 `llm.roles` 覆盖不同模型或服务：

```json
{
  "SourceScout": {"model": "YOUR_SCOUT_MODEL"},
  "EvidenceMapper": {"model": "YOUR_MAPPER_MODEL"},
  "Auditor": {"model": "YOUR_AUDITOR_MODEL", "max_output_tokens": 5000}
}
```

将上面对象作为 `llm.roles` 的值。角色覆盖也支持公共模型配置中的 `provider`、`base_url`、`api_key_env`、`service_egress`、超时等字段。

| 角色 | 实际输入 | 实际工作 | 不能做的事 |
| --- | --- | --- | --- |
| SourceScout | 已抓取页面全文、链接、目标模式和城市 | 提取所有重要条件、职责、开放状态原文和职位信息 | 根据求职偏好伪造页面城市、届别或岗位开放状态 |
| EvidenceMapper | 原文、Scout 提取、本轮固定的经历记录、可选适配器结果 | 每条条件对照、至少 3 项岗位要求与经历对照、经历内容足够时至少 2 条改写、版本和材料行动 | 新增经历、夸大数字和职责、修改经历记录 |
| Auditor | 原文、经历记录、提取内容与匹配建议 | 独立检查遗漏必需条件、资格错判、改写失真，返回批准或问题列表 | 把模型审核当成人工读源核验，改动来源或经历来使判断通过 |

页面、简历、服务返回值都被当作数据，而非可执行指令。程序另行核验原文片段、哈希、引用、模式与数量。模型批准不会自动把 `selected` 改为 `true`。

### 5.4 Codex CLI 作为可选模型入口

本机已经安装并登录 Codex CLI 时，可以将 `llm` 改为：

```json
{
  "provider": "codex_cli",
  "executable": "codex",
  "timeout_seconds": 120,
  "max_attempts": 2
}
```

适配器使用临时目录、`--ephemeral`、`--ignore-user-config`、`--sandbox read-only`，并在提示中要求不调用工具、不读文件。它不会代你登录。此 CLI 可能调用云端模型，因此处理候选人数据也需要 `allow_remote_candidate_data: true`。

`read-only` 主要限制写入，不是禁止读取本机文件的隔离边界；提示中的“不读文件”也不是权限控制。不要把它描述成安全沙箱已经隔离了全部隐私。处理敏感简历时，优先使用权限和网络出口都受到约束的服务，只向模型发送必要经历记录字段；更强隔离需由部署者在操作系统或容器层实现。

## 6. 运行、人工复核和 Word 交付

### 6.1 启动真实流水线

完成实际来源、模型和授权配置后运行：

```bash
python scripts/run_job_match.py doctor --config ../jobmatch-private/config.json
python scripts/run_job_match.py run --config ../jobmatch-private/config.json --candidate ../jobmatch-private/candidate.json --sources ../jobmatch-private/sources.json --output-dir ../jobmatch-private/run-001
```

程序不会在服务不可用时改用模拟数据完成报告。一个岗位失败时，检查点保留错误码，其他岗位可以继续。典型结果：

- `awaiting_review`：资料完整性与来源检查通过，等待人工复核，不等于已确认可投。
- `needs_evidence`：材料、提取、对照或资格检查未通过；应修正输入或补充材料。
- `blocked`：配置、依赖、授权、输出位置等阻止继续。

运行目录包括：

- `checkpoint.json`：请求指纹、每项结果、状态与完成时间。
- `cache/`：可过期的抓取与适配器缓存。
- `report-001.json`：机器分析、岗位资料、经历记录、版本和行动方案。
- `report-001-review.json`：默认全部未批准的人工复核模板。
- `report-001.docx`：资料完整性满足要求时生成的机器分析阶段稿。
- `report-001-result.json`：运行摘要、验证结果、模型调用和缓存统计。

后续运行使用递增文件名，不覆盖旧报告。`report_file` 字段给出本次文件名，不能假设每次都是 001。Word 未生成时，查看 `validation.errors` 和各阶段错误码，不要拿 JSON 存在冒充报告已完成。

### 6.2 人工复核模板

打开本次对应的 `*-review.json`。下面仅说明字段；真实操作保留程序生成的哈希和岗位编号，不要复制这里的占位值：

```json
{
  "report_sha256": "COPY_GENERATED_REPORT_HASH_WITHOUT_CHANGING_IT",
  "approvals": [
    {
      "job_id": "COPY_GENERATED_JOB_ID",
      "source_sha256": "COPY_GENERATED_SOURCE_HASH",
      "source_identity_verified": false,
      "open_status_verified": false,
      "full_jd_reviewed": false,
      "qualification_reviewed": false,
      "rewrite_fidelity_reviewed": false,
      "ready_rewrite_indices": []
    }
  ]
}
```

逐岗实际打开来源，检查来源身份、岗位是否仍开放、JD 是否完整、所有必需条件是否满足、改写是否忠实。完成哪项核查才将哪项改成 `true`。五项全部通过且来源哈希匹配，才可转为已确认岗位。

`ready_rewrite_indices` 是从 0 开始的改写序号，仅填写已经逐句核对的改写。例如 `[0, 1]` 表示批准前两条。`create_first` 不能直接批准为已完成经历；必须先完成相应工作、更新且确认经历记录，再用新运行目录重跑。

报告哈希绑定整个 JSON，不只是文件名。改过报告后不能复用旧复核表。哈希能阻止意外沿用旧审批，但不是数字签名或身份认证；程序无法证明填表的人确实读过网页。

### 6.3 导出复核后 Word

目标数量满足、资料检查通过并且报告必备字段齐全时：

```bash
python scripts/run_job_match.py finalize ../jobmatch-private/run-001/report-001.json --review ../jobmatch-private/run-001/report-001-review.json --output ../jobmatch-private/run-001/reviewed-report.docx
```

已核查公司不足目标，但需要如实交付阶段结果时：

```bash
python scripts/run_job_match.py finalize ../jobmatch-private/run-001/report-001.json --review ../jobmatch-private/run-001/report-001-review.json --output ../jobmatch-private/run-001/reviewed-stage.docx --stage
```

`--stage` 只允许明确呈现数量缺口或未完成的展示字段，不能忽略资料缺失或来源问题。机器通过但未经人工核查的提案会标记“机器分析草稿／未确认可投”，不计入已确认公司数。不得为凑够 20 家重复同一公司、放宽城市或混入实习。

仅对已经准备好的报告 JSON 重新导出时，可使用：

```bash
python scripts/run_job_match.py report ../jobmatch-private/run-001/report-001.json --output ../jobmatch-private/run-001/reexport-stage.docx --stage
```

导出仍执行资料检查，不是跳过 `finalize` 的捷径。每次使用新输出名；程序禁止覆盖原文件。

Word 包含模式、来源统计、全部主清单岗位及机器提案、具体 JD 与投递链接、条件核查、岗位要求与经历对照、改写使用状态、材料出处、简历版本、材料行动、待确认或排除记录、经历记录。正式稿还要求：

- `report_summary`：非空摘要。
- `resume_variants`：每项包含 `id`、`name`、`job_ids`、具体 `changes`。
- `action_plan`：每项包含 `job_id`、具体 `action`、`materials`、`resume_variant_id`，与版本和岗位对应。
- 每个主清单岗位的 `details.salary`、`details.deadline` 和对应模式字段。确实未披露可以明确写未披露；不能把未知薪酬编成一个数字。

正式报告把逐岗修改建议归入不超过 3 套简历结构，通常使用 2–3 套；单一方向可以少于 2 套。各岗位仍保留自己的具体改写和材料出处。流水线先合并同名版本，仍超过 3 套时会额外调用一次 EvidenceMapper 仅做版本分组，不允许改写或丢弃原岗位建议；这次调用也计入执行记录。正式导出拒绝超过 3 套的版本清单。

Word 为 A4、黑白正文 12 pt、表格 10.5 pt，无填充色块，含目录书签、页码和可点击链接。程序检查 OOXML 后原子写出；`render_pending`、`visual_review: pending` 说明尚未完成实际视觉验收。用 Word 或 LibreOffice 打开并逐页检查分页、表格、字体和链接后，才能称为排版验收完成。本 CLI 不自动导出 PDF。

## 7. 12 个适配器的调用方法

下面每节分别给出输入 JSON 和该适配器的配置值。将配置值放入总配置的 `adapters.<组件ID>`；或者独立保存为一个仅含该配置值的 JSON，再传给 `adapter --config`。示例命令使用总配置文件。

独立适配器输出包含 `component_id`、`status`、`data`、`transport`、`upstream_version`、`expected_pin`、`invoked_at`、`cache_hit` 和耗时。实际版本未知时如实返回 `unverified`，源码协议 pin 不能证明远程部署版本相同。

所有输入不写 `data_classification` 时默认视为私有。仅公开 JD、非个人化搜索词和公开评估数据才可以标为 `public`。不要把简历标成公开以绕过隐私开关。

### 7.1 O*NET：`onet_database`

用途：读取固定 31.0 版官方 CSV，按关键词查职业、技能或任务。前提是能访问 O*NET 官方下载地址，不需要模型或额外 SDK。

输入 `inputs/onet.json`：

```json
{"query":"accountant","table":"occupation_data","limit":10,"data_classification":"public"}
```

配置：

```json
{"enabled":true,"cache_seconds":86400}
```

```bash
python scripts/run_job_match.py adapter onet_database --input ../jobmatch-private/inputs/onet.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/onet.json --cache-dir ../jobmatch-private/adapter-cache
```

`table` 可为 `occupation_data`、`essential_skills`、`task_statements`；`limit` 为 1–100。结果保留来源 URL、下载内容 SHA256、总行数与匹配数。关键词是子串筛选，不是语义检索。美国职业描述不能直接当作中国雇主的招聘要求。

### 7.2 ESCO：`esco`

用途：调用官方搜索接口，显式要求版本 1.2.1。无需本地分类模型。输入：

```json
{"query":"accountant","type":"occupation","language":"en","limit":10,"data_classification":"public"}
```

配置：

```json
{"enabled":true,"cache_seconds":86400}
```

```bash
python scripts/run_job_match.py adapter esco --input ../jobmatch-private/inputs/esco.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/esco.json
```

`type` 为 `occupation` 或 `skill`；`language` 为两位语言代码。适配器明确拒绝 `zh`，不会伪造 ESCO 官方中文标签。中文 JD 需要先提供核对过的翻译。接口不可用、版本或返回结构不符时报告错误，不回退成另一版本。

### 7.3 Tabiya 数据集：`tabiya_open_dataset`

用途：读取固定提交中的 ESCO 1.1.1 数据。默认通过固定 GitHub 原始文件地址下载；也可以读取本地干净检出。输入：

```json
{"query":"account","table":"occupations","limit":10,"data_classification":"public"}
```

默认网络配置：

```json
{"enabled":true,"cache_seconds":86400}
```

离线读取前先准备准确版本：

```bash
git clone https://github.com/tabiya-tech/tabiya-open-dataset.git ../tabiya-open-dataset
git -C ../tabiya-open-dataset checkout --detach 815c85d4be9b059c927156d2819e41e904d1d442
```

随后配置可增加 `"checkout":"../tabiya-open-dataset"`。检出必须保持干净；程序读取该提交的 Git blob，不读取任意修改过的 CSV。

```bash
python scripts/run_job_match.py adapter tabiya_open_dataset --input ../jobmatch-private/inputs/tabiya.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/tabiya.json
```

`table` 可为 `skills`、`occupations`、`occupation_skill_relations`，后者可用职业或技能 ID 搜索。此数据的 ESCO 1.1.1 与上一节 1.2.1 是不同版本，不应混写成同一岗位资料。

### 7.4 Docling：`docling`

用途：在本地解析 PDF、DOCX、Markdown、HTML。必须显式安装 SDK；安装助手按注册表固定的源码提交安装，不表示其依赖也获得了完整锁定：

```bash
python scripts/install_optional.py docling --dry-run
python scripts/install_optional.py docling --execute
```

输入：

```json
{"path":"../jobmatch-private/source-document.pdf","data_classification":"private"}
```

配置：

```json
{"enabled":true}
```

```bash
python scripts/run_job_match.py adapter docling --input ../jobmatch-private/inputs/docling.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/docling.json
```

PDF 使用 `NativePdfFormatOption` 的无模型解析路径，不自动下载 OCR 或视觉模型。扫描件、图片页或不完整转换会被拒绝，需要另行获得 OCR 输出并核对原件。结果包含 Markdown 和结构化文档；其中的事实仍需人工形成经历记录。独立 CLI 缓存默认不存私有解析结果，但输出 JSON 本身仍包含私有文本。

### 7.5 Crawl4AI：`crawl4ai`

用途：通过已安装的浏览器 SDK 读取公开页面。前提是 SDK 和 Chromium 资源已经安装，且允许相应下载：

```bash
python scripts/install_optional.py crawl4ai --dry-run
python scripts/install_optional.py crawl4ai --execute
python -m playwright install chromium
```

输入：

```json
{"url":"https://careers.example.org/jobs/position-123","data_classification":"public"}
```

配置：

```json
{"enabled":true,"terms_accepted":true,"timeout":30}
```

```bash
python scripts/run_job_match.py adapter crawl4ai --input ../jobmatch-private/inputs/crawl4ai.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/crawl4ai.json
```

这里的 `terms_accepted` 同样只能在核查条款后设为真。适配器检查 robots，关闭 JavaScript、隐匿模式和持久登录上下文，不重试绕过站点限制。需要 JavaScript 才能显示完整 JD 的页面不会被自动补全。它是独立入口，不会自动替换主流水线的 `sources.fetch`。

### 7.6 JobSpy：`jobspy`

用途：在明确选择的平台上发现招聘线索。先安装 SDK：

```bash
python scripts/install_optional.py jobspy --dry-run
python scripts/install_optional.py jobspy --execute
```

输入：

```json
{"site_name":["linkedin"],"search_term":"accountant","location":"Shenzhen","results_wanted":20,"hours_old":72,"data_classification":"public"}
```

配置：

```json
{"enabled":true,"terms_accepted":true,"cache_seconds":3600}
```

```bash
python scripts/run_job_match.py adapter jobspy --input ../jobmatch-private/inputs/jobspy.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/jobspy.json
```

`site_name` 可选择 `indeed`、`linkedin`、`zip_recruiter`、`glassdoor`、`google`、`bayt`、`naukri`、`bdjobs`。适配器还转发 `google_search_term`、`is_remote`、`job_type`、`country_indeed`；取值需符合该上游平台接口。`results_wanted` 为 1–100。

输出为 `leads`，始终标记 `verification_status: unverified`；移除发现的联系人字段，不抓取 LinkedIn 额外描述。某个平台支持搜索不等于它覆盖中国城市的目标岗位。先检查线索，再把真实官方具体 JD 加入 `sources.json`，没有自动线索入主清单的步骤。

### 7.7 Tabiya 职业分类器：`tabiya_livelihoods_classifier`

前提：按照上游固定提交 `b65781202d6e5c2d70a64aa7778538e3ea70c765` 的说明，自行部署分类服务和模型，实际确认 `/v1/classify` 可用。适配器不部署服务。[上游仓库](https://github.com/tabiya-tech/tabiya-livelihoods-classifier)

输入：

```json
{"text":"Public example JD: prepare accounting reports.","title":"Accountant","options":{"language":"en"},"data_classification":"public"}
```

本地服务配置示例，端口须换成实际值：

```json
{"enabled":true,"service_url":"http://127.0.0.1:8100","allow_local":true,"service_egress":"local_only","service_ready":true,"api_key_env":"JOBMATCH_CLASSIFIER_KEY","timeout":30}
```

```bash
python scripts/run_job_match.py adapter tabiya_livelihoods_classifier --input ../jobmatch-private/inputs/classifier.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/classifier.json
```

实际发送 `text`、`title`、`description`、`options` 中提供的字段，`text` 或 `description` 至少一个非空。`api_key_env` 对应 `x-api-key`，未启用鉴权的本地服务可不配置。返回需要包含 `classification` 和 `metadata`；模型建议不能替代申请条件的判断依据。

### 7.8 ESCO 技能抽取器：`esco_skill_extractor`

前提：按上游提交 `68288f4b3382a07ee32837f33c8bba2ccc2e4d31` 部署服务、分类体系、嵌入模型及 Ollama 模型，并分别核查职业匹配与技能抽取接口。[上游仓库](https://github.com/Datalab-AUTH/esco-skill-extractor)

输入：

```json
{"title":"Accountant","description":"Public example: prepare accounting reports.","qualifications":"Accounting knowledge required.","data_classification":"public"}
```

配置中的两个模型名必须是服务实际已安装的模型：

```json
{"enabled":true,"service_url":"http://127.0.0.1:8200","allow_local":true,"service_egress":"local_only","service_ready":true,"models_ready":true,"llm_provider":"ollama","llm_model":"YOUR_INSTALLED_LLM","embedding_model":"YOUR_INSTALLED_EMBEDDING","max_polls":30,"poll_interval":1,"timeout":30}
```

```bash
python scripts/run_job_match.py adapter esco_skill_extractor --input ../jobmatch-private/inputs/esco-extractor.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/esco-extractor.json
```

调用顺序是 `/api/occupation/match` → 轮询 `/api/jobs/{job_id}` → 选择第一条职业候选 → `/api/skills/extract` → 再次轮询。职业匹配为空时返回空结果，不伪造技能。`max_polls` 为 1–300，`poll_interval` 为 0–30 秒，服务一直未完成时返回超时。

该适配器只支持预部署的 Ollama 路径，不把云 API 密钥放进可能持久化的任务正文。服务会保留任务文本；不使用本地结果缓存来重放任务。`service_ready` 和 `models_ready` 都不是自动安装开关。

### 7.9 Tabiya Compass：`tabiya_compass`

前提：部署并配置提交 `8470bea7f68d0990898f441a12bbb6a261a0ef34` 对应的服务，已有授权会话和模型。适配器不创建用户、会话或登录。[上游仓库](https://github.com/tabiya-tech/compass)

输入中的 `session_id` 必须指向你的真实授权会话，示例 12 不能直接套用：

```json
{"session_id":12,"user_input":"模拟经历：在课程项目中整理问卷数据。","data_classification":"private"}
```

配置：

```json
{"enabled":true,"service_url":"http://127.0.0.1:8300","allow_local":true,"service_egress":"local_only","service_ready":true,"token_env":"JOBMATCH_SERVICE_TOKEN","timeout":30}
```

```bash
python scripts/run_job_match.py adapter tabiya_compass --input ../jobmatch-private/inputs/compass.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/compass.json
```

请求 `/conversations/{session_id}/messages?filter_pii=true`。`token_env` 对应 Bearer Token；服务无此鉴权要求时可不设置。输入始终按私有处理，即使错误标成公开也不能绕过。会话会保存在上游，不启用适配器结果缓存。发现的技能仍待候选人确认，不能自动写入已确认经历记录。

### 7.10 Resume Matcher：`resume_matcher`

前提：部署提交 `ab2b370d1fb98d9272f71b8d6e54a9c51ac75110` 对应服务、模型和存储，已在授权范围内创建简历与岗位记录。当前适配器只调用已有记录的预览，不负责上传或建档。[上游仓库](https://github.com/srbhr/Resume-Matcher)

输入：

```json
{"resume_id":"YOUR_EXISTING_RESUME_ID","job_id":"YOUR_EXISTING_JOB_ID","data_classification":"private"}
```

配置：

```json
{"enabled":true,"service_url":"http://127.0.0.1:8400","allow_local":true,"service_egress":"local_only","service_ready":true,"token_env":"JOBMATCH_SERVICE_TOKEN","timeout":60}
```

```bash
python scripts/run_job_match.py adapter resume_matcher --input ../jobmatch-private/inputs/resume-matcher.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/resume-matcher.json
```

可选 `prompt_id` 选择上游已有提示。实际调用 `/api/v1/resumes/improve/preview`，结果须含 `data.resume_preview`。仅生成预览，不由此适配器保存改写后的简历，也不自动批准为可投版本。始终按私有处理且不缓存重放；ATS 分数不等于录用概率。

### 7.11 MELO：`melo_benchmark`

用途：调用固定上游评估器评估你提供的分数矩阵和相关性标签。它不会训练模型或为你生成预测。

先准备干净检出；上游 `trec_eval` 二进制需要兼容的 Linux 环境。请按对应上游版本说明把 Python 依赖安装在仓库外的虚拟环境，不在检出目录生成构建文件：

```bash
git clone https://github.com/Avature/melo-benchmark.git ../melo-benchmark
git -C ../melo-benchmark checkout --detach 014982fef0abf5149b16e75b1043bd286ae4c98f
```

输入是模拟矩阵：

```json
{"query_ids":["q1"],"corpus_ids":["c1","c2"],"scores":[[0.9,0.1]],"relevance":{"q1":{"c1":1,"c2":0}},"data_classification":"public"}
```

配置：

```json
{"enabled":true,"checkout":"../melo-benchmark","timeout":60}
```

```bash
python scripts/run_job_match.py adapter melo_benchmark --input ../jobmatch-private/inputs/melo.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/melo.json
```

可选 `python` 必须填写已经安装依赖的 Python 可执行文件路径，不能只写 `python3` 名称；不配置时使用当前解释器。查询与岗位资料 ID 必须是安全 ASCII 标识，矩阵为“查询数 × 岗位资料数”，值必须有限。每个查询至少有一个正相关标注。检出提交不符、有修改、依赖或平台不兼容会失败，不生成假的评估分数。

这个小输入只能测试调用；不能称为已跑完整 MELO 基准，更不能推出中国求职场景效果。

### 7.12 TGRE：`tgre_classification`

用途：调用上游 `classification/compute_scores.py` 对提供的职业或技能预测排名计算指标；不运行上游模型推断。

```bash
git clone https://github.com/aekpalakorn/TGRE-Classification.git ../tgre
git -C ../tgre checkout --detach 91fccb7a513edc8e069491052bf5979f7cac4525
```

按该提交的说明在仓库外环境安装评估脚本所需依赖，保持检出干净。输入：

```json
{"task":"occupation","instances":[{"gold":["accountant"],"predicted":["accountant","bookkeeper"]}],"data_classification":"public"}
```

配置：

```json
{"enabled":true,"checkout":"../tgre","timeout":60}
```

```bash
python scripts/run_job_match.py adapter tgre_classification --input ../jobmatch-private/inputs/tgre.json --config ../jobmatch-private/config.json --output ../jobmatch-private/adapter-results/tgre.json
```

`task` 为 `occupation` 时计算上游 `precision@1`；为 `skill` 时计算上游的 3、5、10 截断指标。输入含 1–1000 条实例，每项 `gold` 与 `predicted` 均为非空标签列表。上游归一化后标签为空或重复会拒绝。可选 `python` 与 MELO 相同。合成标签上的高分不能冒充真实求职报告的准确率。

### 7.13 服务隐私开关的共同规则

分类器、ESCO 抽取器、Compass、Resume Matcher 的 `service_url` 是实际部署地址，不是 GitHub 地址。`allow_local: true` 只允许显式 loopback 服务，并不允许任意内网或云元数据地址。

私有输入只有以下情况之一才允许发送：

- loopback 服务且声明 `service_egress: local_only`，部署者已经核查它不会转发数据。
- 已获授权并显式设置该适配器的 `allow_external_private: true`。

本地代理也可能调用云模型，不能仅凭地址是 `127.0.0.1` 认定数据不出本机。API 服务对提交内容的存储、日志、备份和保留时间，需要单独检查；客户端不保证替上游删除数据。

## 8. 把适配器接入逐岗分析

`adapter` 命令用于独立执行。要在每个完整、开放且模式匹配的 JD 与经历对照之前自动补充参考，可配置 `adapter_steps`，并在 `adapters` 为相应组件显式启用。

```json
{
  "adapters": {
    "esco": {"enabled":true,"cache_seconds":86400},
    "tabiya_livelihoods_classifier": {
      "enabled":true,
      "service_url":"http://127.0.0.1:8100",
      "allow_local":true,
      "service_egress":"local_only",
      "service_ready":true
    }
  },
  "adapter_steps": [
    {
      "component":"esco",
      "input":{"query":"accountant","type":"occupation","language":"en","limit":5,"data_classification":"public"}
    },
    {
      "component":"tabiya_livelihoods_classifier",
      "input":{"text":"$source.text","title":"$scout.title","data_classification":"public"}
    }
  ]
}
```

上面只是应合并入主配置的片段。`$source` 可访问 `url`、`text`、`links`；`$scout` 可访问提取字段；`$candidate` 是整个本轮固定的经历记录列表。绑定只能读取实际存在的字典路径，不提供任意表达式、模板执行或数组索引功能。输入引用 `$candidate` 时自动标为私有。

此机制把适配器结果作为 Mapper 的补充参考，保留调用版本和缓存状态；不会把分类器输出自动升级为“雇主要求”或“候选人已掌握技能”。不要把所有 12 项放进每个岗位的步骤：文档解析、会话、已有简历预览、基准评估有不同输入和副作用，通常应独立执行。任何启用步骤失败会使该岗位任务记录错误，程序不会悄悄忽略它。

## 9. 缓存、恢复和保留策略

缓存键包含组件、版本、输入与配置；Docling 另计源文件 SHA256。命中后保留原采集时间，不能把旧页面包装成刚核查过。缓存文件有内容哈希和过期时间；损坏或到期视为未命中。

默认时间：公开来源 3600 秒，最多 24 小时；robots 3600 秒；适配器结果默认 24 小时、最多 7 天。Compass、Resume Matcher 和 ESCO 抽取任务不使用结果缓存，避免会话或任务被错误重放。私有适配器结果仅在主流水线 `cache_candidate_data: true` 时允许缓存，独立 `adapter --cache-dir` 默认不启用私有缓存。

同样的配置、经历记录、来源和提示版本，可以重用相同 `--output-dir` 恢复未完成工作。已完成项只有在原采集时间不足 24 小时时才恢复；过期或失败项重试。请求指纹改变时拒绝复用旧目录，使用新的运行目录。`refresh_sources: true` 会重新抓取，但配置本身改变也会改变指纹，因此切换该值时应使用新目录。

检查点与报告本身一直含有候选人经历记录和分析结果。`cache_candidate_data: false` 只关闭额外私有缓存，不等于不写盘、不保存简历信息或已经加密。运行目录权限为 0700，JSON 采用私有临时文件原子写入；权限控制不是磁盘加密，也不能阻止管理员、备份软件或共享账号读取。按实际数据授权范围配置磁盘、备份与保留策略。

清理过期缓存：

```bash
python scripts/run_job_match.py cache-prune --cache-dir ../jobmatch-private/run-001/cache
```

该命令删除到期或损坏的缓存项，返回 `removed_cache_entries`，不删除报告或检查点，也不承诺安全擦除。无需或无权继续保存时，应由操作者明确选择需要删除的私有材料。不要用公开仓库提交保存运行档案。

`.run.lock` 防止同一运行目录被两个进程同时写入。进程异常结束留下锁时，先确认没有任务仍在运行，再处理这个具体锁文件；不要因为看见锁就直接清空目录。

## 10. 验证什么，如何描述验证结果

运行本地自动测试：

```bash
python -m unittest discover -s scripts -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

测试中的 SDK double、本地 HTTP fixture、合成岗位和模型返回值，验证的是协议、检查要求、缓存、恢复、调用次序和导出结构，不是第三方真实服务的效果。测试全部通过不能写成“12 个服务都已安装并成功运行”。

每个真实适配器另行执行一次获准的小样本，并保存私有结果里的 `transport`、`upstream_version`、`expected_pin`、`invoked_at` 和 `cache_hit`。需要确认刚调用过上游时，不传缓存目录或使用新的缓存目录；缓存命中本身不证明当前上游可用。远程接口版本返回 `unverified` 时，报告中也保留该状态。

真实三角色运行还需查看 `execution.model_calls`：有对应角色、实际模型、重试次数、耗时及 `execution: real_model_call`。这说明执行过模型请求，不说明模型判断必然正确。完整效果评估另需独立标注样本、明确任务与指标、失败样本和结果复现记录。

常见问题的处理顺序：

| 结果或错误码 | 核查方向 |
| --- | --- |
| `component_disabled` | 是否只为需要的适配器显式设置 `enabled: true` |
| `DEPENDENCY_MISSING` / `missing_dependency` | 所用 Python 环境是否装有对应 SDK 或评估依赖，检出路径是否正确 |
| `SERVICE_NOT_READY` / `MODEL_NOT_READY` | 先部署并实际检查上游；不要只改布尔值假装就绪 |
| `privacy_consent_required` / `PRIVACY_CONSENT_REQUIRED` | 数据是否会离开本机，是否已有授权，是否应改用受限本地服务 |
| `source_not_allowed` / `robots_denied` | 核查来源范围和条款，必要时选择其他授权来源，不绕过 |
| `invalid_model_json` / `invalid_stage_output` | 检查模型是否遵循 JSON 合同，记录失败，不使用模拟结果补齐 |
| `unsupported_excerpt` / `unsupported_candidate_claim` | 回到来源或候选人经历记录补充材料，不能删掉检查规则 |
| `resume_mismatch` / `stale_review` | 输入改变后用新目录重跑、重新复核，不复用旧审批 |
| `version_mismatch` / `dirty_checkout` | 使用规定提交的干净检出；不在上游检出中生成临时文件 |
| `render_pending` | 打开 Word 逐页验收；这不是已完成视觉检查的状态 |

程序没有设置一个“一键让所有岗位通过”的开关。现有材料不足时留下准确的失败状态，是正常结果的一部分。
