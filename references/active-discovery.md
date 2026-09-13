# 主动岗位发现

这个流程用于“给一份简历，主动找出适合的岗位”。搜索结果只用于发现，不能直接进入可投清单。

## 1. 先固定三个条件

`intake.json` 必须包含一个招聘模式、明确城市和1—5个已确认职能方向：

```json
{
  "employment_mode": "campus_full_time",
  "locations": ["杭州", "广州", "深圳"],
  "target_directions": ["财务分析", "管理会计", "审计"],
  "recruitment_season": "autumn"
}
```

方向应当是招聘中会出现的职能语言，不是“好公司”、“发展好”或一长串个性形容词。如果用户未指定，先从已确认经历中提出最多5个候选方向，说明每个方向由哪些经历支撑，再请用户选择。

## 2. 生成可复查的检索计划

```bash
python scripts/prepare_job_match.py plan --intake ../jobmatch-private/intake.json --output ../jobmatch-private/search-plan.json
```

计划对“方向 × 城市”生成有编号的检索式。使用当前会话可用的搜索工具逐条执行；单次工具有查询数限制时分批执行。优先收集雇主官网、官方 ATS、政府、高校和国际组织招聘页；聚合页仅用于找到具体职位链接。

保存结果时一项一行，不要将简历文本写入搜索结果：

```json
[
  {"query_id":"q-001","url":"https://careers.example.org/jobs/123","title":"结果标题","snippet":"搜索摘要"}
]
```

示例域名不是真实岗位。不得伪造未执行的搜索结果。

## 3. 导入待复核候选池

```bash
python scripts/prepare_job_match.py search-results --results ../jobmatch-private/search-results.json --intake ../jobmatch-private/intake.json --output ../jobmatch-private/source-draft.json
```

导入会按规范URL去重，并保留来自哪个方向和城市查询。它不请求职位页，也不判断在招。

逐条打开候选页后，`source-review.json` 除了原有的具体JD、范围与来源核对，还要确认方向：

```json
{
  "draft_sha256":"使用当前草稿摘要",
  "human_confirmed":true,
  "sources":[
    {"id":"候选链接ID","specific_jd_confirmed":true,"scope_confirmed":true,
     "direction_confirmed":true,"source_tier":"employer_official"}
  ]
}
```

导出时会将已复核职位所在主机名加入本次允许范围。抓取条款和人工复核仍然保留，不因搜索结果可访问而自动放行。

## 4. 覆盖与停止条件

记录每个方向的查询数、线索数、具体JD数、在招数和资格通过数。达到20家不同雇主且逐条完成复核后停止。若确认的方向与地区范围已执行完整检索仍不足20家，交付阶段报告，列出差额和排除原因；不自动改城市、招聘模式或方向来凑数。

“全网”表示跨多个公开来源执行计划内检索，不表示穷尽互联网所有页面。搜索工具、登录和站点条款限制必须在报告中说明。
