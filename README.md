# Social Science Job Match Skill

这是一个证据驱动的求职报告 Skill。它先区分实习、应届校招全职和社招全职，再按候选人事实、实时JD与同批语料指导报告制作。

当前已实现语料汇总、原文指纹与摘录检查、候选人证据引用检查、报告记录与语料交叉检查。12个第三方组件仍处于documented_only状态；尚未实现第三方调用适配器、自动缓存或Word生成流水线。人工/Agent执行流程见[运行与评估](references/run-and-evaluate.md)。

## 报告依据与当前实现

- 每个“可投”结论都回到具体 JD 和候选人证据编号。
- 主清单只接受雇主官网、官方 ATS 或身份与详情均可核验的雇主直发来源。
- 岗位语料按招聘模式、地区和时间窗分组；单批语料只支持本批样本观察。
- O*NET、ESCO 和开源模型只用于职业归一化、技能发现、解析或评估，不决定候选人资格。
- 聚合站、搜索摘要、Agent 转述和相似度分数不能替代人工审计。

## 统一开源证据栈

开源项目及固定版本记录在 [`integrations/open_source_stack.json`](integrations/open_source_stack.json)，接入规则见 [`integrations/README.md`](integrations/README.md)。仓库不复制第三方源码，也不自动安装重型依赖；所有组件先转换为统一语料记录，再进入同一个验证与报告流程。

以下是计划中的组件协作关系，并非已跑通的自动流水线。

```text
简历/PDF JD ── Docling ───────────────┐
公开官方页面 ─ Crawl4AI/人工读取 ─────┤
境外平台线索 ─ JobSpy（仅发现）────────┤
                                      ├─ 统一岗位语料
O*NET/ESCO ─ 分类与技能基准 ───────────┤
Tabiya/ESCO extractor ─ 候选映射 ─────┤
Compass ─ 经历证据补录 ───────────────┘
                    ↓
          证据映射 → 人工审计 → 结构闸
                    ↓
         20家公司JD与定向简历修改报告
```

## 本地检查

```sh
python3 scripts/validate_integrations.py integrations/open_source_stack.json
python3 scripts/build_evidence_basis.py corpus.json --output evidence-new.json
python3 scripts/validate_jobs.py jobs.json
python3 -m unittest discover -s scripts -p 'test_*.py'
```

`build_evidence_basis.py`只汇总已整理的语料，不联网、不抓取、不上传文件。第三方许可证与归属说明见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。
