# Social Science Job Match Skill

这套 Skill 根据简历和当前招聘信息制作求职报告。它先区分实习、应届校招全职和社招全职，再逐项对照岗位要求，给出适合投递的岗位和简历修改建议。

现已包含12个第三方调用适配器、带TTL的内容缓存与断点续跑、三角色独立模型调用，以及结构化数据到Word草稿、人工复核、正式Word的流水线。注册表状态为 `adapter_implemented`。这表示代码已实现，不表示12个上游都已部署、联网通过或完成求职效果评估。逐项验证记录见 [runtime-verification.json](integrations/runtime-verification.json)。

先读 [可执行运行手册](references/runtime-guide.md)。默认不启用第三方组件、不允许私人候选资料出站；按需配置模型、服务和依赖。招聘模式必须先选实习、应届全职或社招全职。

首次输入可使用 [简历与岗位入口准备](references/preparation-entry.md)：本地提取 PDF、DOCX 或文本简历，从指定公开招聘入口发现链接，分别经过人工确认后导出运行所需文件。它是有范围限制、保留人工确认的准备流程，不是通用全网搜索或自动投递。

运行前用 [上游运行条件检查](references/deployment-checks.md) 区分已安装、已配置、可访问、实际调用与效果评估。检查不会自行部署、启用模型或替代业务验证；历史验证单列日期。可选补充工具可明确设置 `on_failure: "continue"`，仅对约定的可用性故障继续，报告保留缺失说明；隐私、地址安全和无效输出仍停止处理。

## 报告依据与当前实现

- 每个“可投”结论都回到具体 JD 和经历编号。
- 主清单只接受雇主官网、官方 ATS 或身份与详情均可核验的雇主直发来源。
- 岗位资料按招聘模式、地区和时间窗分组；单批岗位资料只支持本批样本观察。
- O*NET、ESCO 和开源模型只用于职业归一化、技能发现、解析或评估，不决定候选人资格。
- 聚合站、搜索摘要、Agent 转述和相似度分数不能替代人工复核。

## 开源工具与数据来源

开源项目及固定版本记录在 [`integrations/open_source_stack.json`](integrations/open_source_stack.json)，接入规则见 [`integrations/README.md`](integrations/README.md)。仓库不复制第三方源码，也不自动安装重型依赖；所有组件先转换为统一岗位资料记录，再进入同一个验证与报告流程。

下面是按需调用关系。`run` 读取你提供的具体来源列表，不自动穷尽全网；JobSpy的发现结果仍需回到官方JD核验。每份报告只记录实际调用过的组件。

```text
简历/PDF JD ── Docling ───────────────┐
公开官方页面 ─ Crawl4AI/人工读取 ─────┤
境外平台线索 ─ JobSpy（仅发现）────────┤
                                      ├─ 统一岗位资料
O*NET/ESCO ─ 分类与技能基准 ───────────┤
Tabiya/ESCO extractor ─ 技能匹配建议 ─────┤
Compass ─ 补充经历说明 ───────────────┘
                    ↓
       SourceScout → EvidenceMapper → Auditor
                    ↓
            Word草稿 → 人工复核 → 完整性检查
                    ↓
         20家公司JD与定向简历修改报告
```

## 本地检查

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/run_job_match.py doctor
python scripts/check_runtime.py
python scripts/prepare_job_match.py --help
python3 scripts/validate_integrations.py integrations/open_source_stack.json
python3 scripts/build_evidence_basis.py corpus.json --output evidence-new.json
python3 scripts/validate_jobs.py jobs.json
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
```

`build_evidence_basis.py`仍只做本地岗位资料汇总；联网入口在 `run_job_match.py`。`doctor`只检查配置，不调用服务或认证模型。可选SDK的固定版本安装入口是 `scripts/install_optional.py`，默认仅预览安装计划。第三方许可证与归属说明见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

Word正文12pt、表格10.5pt，白底无填充色，含逐岗JD、条件对照、改写位置、经历编号、简历版本和行动计划。文件写出后返回 `render_pending`，仍须逐页渲染检查；未复核岗位不能计入20家正式报告。仓库只包含代码与合成测试，不含个人简历或求职报告。
