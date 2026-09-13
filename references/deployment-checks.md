# 上游运行条件检查

这份检查补充已有 doctor 的配置检查，不安装软件、不下载模型、不启用任何组件，也不宣称全部部署完成。采用五个独立字段，避免把安装、连通和有效性混为一谈。

| 字段 | 含义 | 不能据此推断 |
| --- | --- | --- |
| installed | 当前解释器可找到 SDK，或评估代码目录存在；远程服务及未核查的数据安装状态为 null | 固定版本已验证、模型已准备 |
| configured | 最低配置声明存在；内置公共资料客户端不需要额外地址配置 | 配置已生效、服务能正常工作 |
| reachable | 本次显式检查时 HTTP 端点有响应；错误响应也为 true，具体状态码另列 | 健康检查通过、业务接口可用 |
| live_operation_verified | 本次业务调用是否验证；该检查器不执行业务调用，因此为 null | 历史测试结果适用于当前环境 |
| effectiveness_evaluated | 本次是否评估匹配效果；该检查器不评估，因此为 null | 安装数量越多，报告越准确 |

null 表示未核查或不适用，不能显示成“通过”。enabled 与上述字段分开记录，默认仍为 false。SDK 检查只查模块位置，不导入第三方库，以免初始化时自动下载模型。评估代码目录存在，也不代表版本或依赖已经验证。

## 使用方式

在 Skill 根目录，使用为项目准备的 Python 解释器运行：

```bash
python scripts/check_runtime.py
python scripts/check_runtime.py --config runtime-config.json --output readiness.json
```

默认不联网、不解析 DNS，输出各组件具体缺项及 standard / enhanced / evaluation 三类能力清单。输出路径必须是新文件；文件权限按私人资料处理。退出码 0 只表示检查顺利生成，不表示组件全部就绪。配置错误或输出失败返回 2。

确实需要检查服务 HTTP 连通时，明确配置上游维护者提供的只读健康检查路径，然后执行：

```bash
python scripts/check_runtime.py --config runtime-config.json --live --output readiness-live.json
```

配置片段（示例只指向本机，不代表已部署服务）：

```json
{
  "adapters": {
    "tabiya_livelihoods_classifier": {
      "enabled": false,
      "service_url": "http://127.0.0.1:8000",
      "allow_local": true,
      "health_path": "/health",
      "health_timeout": 5,
      "service_ready": false
    }
  }
}
```

`/health` 必须替换成当前部署实际提供的只读端点，检查器不会猜测路径。`--live` 只检查四类已配置 HTTP 服务；不会调用分类、会话、简历优化接口，不携带简历或 JD，也不测试 SDK、公共数据接口或评估工具。组件未启用也可以诊断，诊断不会修改 enabled。

远程服务只允许 HTTPS；HTTP 仅允许显式开启的本机回环地址。沿用 URL 安全检查，拒绝私网、保留地址和跨主机跳转。健康检查超时 1–10 秒，响应最多 64 KiB，不保存响应正文。配置凭据仅允许 api_key_env / token_env 环境变量名，不打印变量内容。不绕过 ESCO 当前环境的保留地址限制。

HTTP 401、403、404、503 等会保留状态码并列出待解决事项。HTTP 200 也仅表示收到响应，即使响应写着 model_ready，也不会升级为业务调用通过。模型实际调用可能产生费用或保存内容，必须单独准备合成样本、预算与授权后验证。

## 三类能力与验收顺序

| 清单 | 组件 | 需要落实的工作 |
| --- | --- | --- |
| standard | Docling、Crawl4AI、O*NET、ESCO、Tabiya 开放数据 | 在隔离环境安装固定版本 SDK；核对浏览器资源与简历解析结果；实际取得职业资料并核对版本和校验值；核对官方 JD；人工复核 Word |
| enhanced | JobSpy、Tabiya 分类器、ESCO 技能提取器、Compass、Resume Matcher | 按需部署；配置模型和必要会话或记录；确认预算、资料留存与联网行为；分别验证实际业务响应 |
| evaluation | MELO、TGRE | 固定上游与评估输入；准备兼容环境；明确区分评分函数演示与完整效果评估 |

清单是能力分组，不是“一键全开”的预设。standard 中的职业资料也是辅助参考，不替代当前招聘页；无需为每份报告运行所有数据源。JobSpy 的结果只用于发现线索。MELO 固定版本所带的 trec_eval 是 Linux x86_64 二进制，本机不兼容时需要独立评估环境。无需让它阻塞日常报告。

## 历史记录与当前检查

检查器读取 integrations/runtime-verification.json，将其中组件状态及 recorded_at 日期单独置于 historical_verification。该信息来自版本库历史记录，不代表当前环境的复测结果，applies_to_current_environment 固定为 false。当前检查时间单独为 checked_at，不把两者合并。

提交或发布诊断结果前应自行检查内容。检查器不会复制服务地址、凭据或原始响应，但项目完整运行配置仍可能含本机路径和账号信息，不应直接公开。

本检查器配有 SDK 缺失模拟、被动模式不联网、HTTP 错误码、安全地址限制、历史时间隔离及凭据保护测试。这些本机合成测试不等于上游服务已部署，也不构成求职匹配效果研究。
