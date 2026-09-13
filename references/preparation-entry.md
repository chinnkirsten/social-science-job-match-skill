# 简历与岗位入口准备

入口：`scripts/prepare_job_match.py`。它把本地简历和指定招聘入口转换成既有报告程序接受的文件，中间保留两处人工确认。无需启用全部第三方组件。

## 1. 明确招聘类型，再解析简历

`intake.json` 的最低内容：

```json
{"employment_mode":"campus_full_time","locations":["杭州","广州","深圳"],"target_directions":["财务分析","管理会计","审计"],"recruitment_season":"autumn"}
```

招聘类型须由用户选定：`internship` 实习、`campus_full_time` 校招正式岗、`experienced_full_time` 社招正式岗。不能根据学历猜测。`target_directions` 保存1—5个已确认职能方向；可以先解析简历再确认，但方向缺失时不得开始岗位发现。校招季可选 `autumn`、`spring`、`unspecified`；实习和社招只用 `unspecified`。毕业日期、到岗安排、最低薪酬可分别填写 `graduation_date`、`availability`、`minimum_pay`；未提供记待确认，不自动填“不限”。

```bash
python scripts/prepare_job_match.py resume --resume resume.pdf --intake intake.json --output resume-draft.json
```

默认使用本地解析：TXT/Markdown 直接读取，PDF 使用 `pypdf`，DOCX 使用 `python-docx`。新环境须先安装所选解析器依赖；本程序不自动安装。扫描件或空白 PDF 页会暂停，要求核对或先做本地 OCR。多栏阅读顺序、图中隐藏文字等仍需对照原件检查。DOCX 表格单独附在段落后，不能当作原版排版顺序。

如要使用 Docling，在 `--config` 文件中设 `resume_parser: "docling"` 并明确启用该适配器；仍是本地无模型下载解析。加 `--use-model` 可使用既有模型配置标出可能与经历相关的行；不会改写、删除或确认原文。远程模型处理简历必须另有明确授权 `allow_remote_candidate_data: true`，本地 CLI 不等于本地推理。

输出全部是待确认记录，尚无可直接交给报告程序的 `candidate_evidence` 字段。草稿可能含简历个人信息，只保存在私有工作目录，不上传 GitHub。

准备入口会拒绝将草稿、导出结果或缓存写进公开 Skill 目录，须选择仓库外的私有位置。该检查覆盖路径解析后的目标，不只是文件夹名称。

## 2. 主动搜索或指定招聘入口

从简历主动找岗时，按[主动岗位发现](active-discovery.md)先生成检索计划，用当前可用的网络搜索工具执行，再导入结果：

```bash
python scripts/prepare_job_match.py plan --intake intake.json --output search-plan.json
python scripts/prepare_job_match.py search-results --results search-results.json --intake intake.json --output source-draft.json
```

这两步保留“方向 × 城市”的查询编号，但不内置通用搜索服务，也不伪造未执行的结果。导入的URL只是待复核线索。

已知雇主或招聘入口时，也可以用下面的一层链接发现。

`seeds.json` 是 1 至 10 个获准访问的公开招聘列表页：`[{"url":"https://careers.example.test/jobs"}]`。该域名仅演示格式，不是真实岗位。配置沿用报告程序的 `allowed_source_hosts`、`source_terms_accepted`，另外加上相同的招聘类型与城市。可设置 `discovery_url_contains: ["/jobs/"]` 筛选链接 URL，`discovery_max_links` 限制数量（1 至 300，默认 100）。URL 筛选不是语义岗位匹配。

```bash
python scripts/prepare_job_match.py discover --seeds seeds.json --config config.json --cache-dir private-cache --output source-draft.json
```

每个入口只读取一层链接，沿用域名范围、robots 检查、缓存、访问限制。跨域链接只在明确允许的域名内保留；官方招聘系统的其他域名必须事先列入范围。不登录、不绕验证码、不执行页面 JavaScript。动态页面可能无法提取，应人工提供可访问的具体 JD 或使用另行配置的采集能力。

结果是待核对链接，不是“可投岗位”。保留获取时间、失败代码、数量是否截断；访问失败不解释为岗位关闭。主动发现依赖执行时可用的搜索工具，程序本身不承诺穷尽全网。

## 3. 人工确认后导出

核对 `resume-draft.json`，单独创建 `resume-review.json`：

```json
{
  "draft_sha256":"填入草稿的摘要",
  "human_confirmed":true,
  "intake_confirmed":true,
  "records":[
    {"id":"exp-0001","keep":false},
    {"id":"exp-0002","keep":true,"text_verified":true,"state":"ongoing"}
  ]
}
```

每行必须明确保留或删除，联系方式和无关标题一般删除。保留行必须逐条核对原文并选择 `completed`、`ongoing`、`planned` 或 `unconfirmed`。计划经历不得确认为已完成。需要修改原文时先修正本地解析输入并重新准备，旧确认自动失效。`human_confirmed` 必须来自真实人工确认，模型或脚本不能代填为已确认。

逐条打开要保留的招聘链接，创建 `source-review.json`：

```json
{
  "draft_sha256":"填入来源草稿的摘要",
  "human_confirmed":true,
  "sources":[
    {"id":"所选链接的id","specific_jd_confirmed":true,"scope_confirmed":true,"direction_confirmed":true,"source_tier":"employer_official"}
  ]
}
```

`scope_confirmed` 表示人工已核对招聘类型、城市、校招季及届别与意向一致。搜索结果携带方向时还必须设置 `direction_confirmed: true`；一层入口未携带方向时无此字段。现有报告模型会检查招聘类型和城市，但校招季、届别不能只靠该配置字段自动保证。来源等级沿用既有五档；聚合平台不能冒充企业官网。

```bash
python scripts/prepare_job_match.py export --resume-draft resume-draft.json --resume-review resume-review.json --source-draft source-draft.json --source-review source-review.json --config config.json --output-dir prepared
python scripts/run_job_match.py run --config prepared/config.json --candidate prepared/candidate.json --sources prepared/sources.json --output-dir report-run
```

导出要求两份草稿的招聘意向完全一致，且两份人工确认与当前草稿摘要对应。输出目录不可覆盖旧目录，目录权限为 0700、JSON 文件为 0600。输出 `candidate.json`、`config.json`、`sources.json` 与既有报告入口兼容。导出成功只表示输入准备完成；在招状态、申请条件、逐岗建议和 Word 报告仍由后续流程处理，最终报告仍需人工复核。

本入口没有改变旧的底层 `run_job_match.py` 接口；技术用户仍可直接提供按既有规范整理的输入。摘要用于防止误用过期确认，不是身份认证或数字签名。

## 验证范围

`tests/test_preparation_runtime.py` 使用明确标注的非个人测试文本，实际生成并读取临时 PDF、DOCX，检查招聘模式、隐私授权、原文保留、确认绑定、文件权限及导出兼容性。招聘页面采集使用模拟响应验证范围和数量控制，不代表真实招聘网站已测通，也不代表个人求职效果已经评估。
