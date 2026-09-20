# Companion 预设设计

## 已落地：Data 是官方预设的唯一内容来源

`eidolon_data/resources/companion_presets/` 中每个 JSON 是一份完整的
`PersonaPreset`，`catalog.json` 的 `presets` 决定展示顺序。服务使用
`importlib.resources` 加载安装包内资源，不依赖进程工作目录或 SDK 默认人格。
加载时检查完整字段、唯一 ID、文件清单、版本和示例；错误直接暴露，不退回另一套模板。

SDK 只保留跨进程模型 `PersonaPreset` / `PersonaPresetCatalog`，不再提供目录工厂或
任何官方模板内容。Admin 继续转发 Data 接口，Mobile/Web 读取同一份目录。
修改模板内容必须提升 `revision`。创建沿用完整人格快照写入流程，更新模板不会
覆盖已创建伙伴。

来源模板会记进 provenance：`source_preset_id` 与 `source_preset_revision`。
**它只是记录。** 人格在创建时整份写定，所以提升模板 `revision` 不会回溯已创建的
伙伴；没有任何读取路径会去解析这两个字段来决定行为，伙伴此后的自进化与模板无关。
声明由客户端给出——只有它持有草稿、知道用户有没有改过；Data 只记录，不比对推断。

### 怎么读这份 provenance

`origin` 一个词不够用，必须和 `source_preset_id` 一起读。四种形状：

| origin | source_preset_id | 含义 |
| --- | --- | --- |
| `template` | 有 | 原封不动取用了这份官方模板 |
| `template` | 无 | SDK 内置默认人格（旧版 onboarding，或创建时没带 persona 的兼容入口） |
| `owner_authored` | 无 | 从零写的，或首次创建前已修改模板（当前此路径未保留原模板 ID） |
| `owner_authored` | 有 | 从这份模板起步，之后被改写过 |

第四种是**正常且有意**的：`source_preset_id` 说的是 v1 从哪来，`origin` 说的是当前
这一版怎么来的。编辑、改名、恢复都会把 `source_preset_id` 继续带给后代版本，因为
"它从哪开始"不会因为后来被改过就不再为真。

genome 行的 `source_json.source_type` 画同样三条线（`companion_preset` /
`owner_authored` / `companion_provision`），不是两条。`persona_service` 会从
`source_type` 反推 `origin`，两列若能各说各话，同一件事就成了两件事。

### 早于本次修正的既有行

`pi5-preset-provenance-20260918b` 期间建出来的伙伴，`origin` 已经是 `template`，
但 `source_type` 仍写着 `owner_authored`——那一版只改了两列中的一列。没有读取路径
依赖这一列（只有 `owner_restore` 被读到），所以行为不受影响，但按上表去读这些行会
在第一列和第三列之间看到矛盾。要统一得补录，而补录**不能重算 `genome_hash`**：
会话把 genome_hash 钉在对话元数据里，改了它，正在进行的对话会在下一轮直接失败。

### Data 只拒绝不可能为真的声明

不比对正文去猜"这份人格是不是那个模板"——那种推断在悄悄出错之前一直是对的。
能拒的只有当面就假的声明，两条：没有 `source_preset_id` 的 `source_preset_revision`
（一个不属于任何模板的版本号），以及不带 persona 的模板声明（那会把主机自己造的
默认人格标成用户选的模板）。有 id 无 revision 是**残缺但为真**，因此放行——
"哪份模板被选了"这个问题，光靠 id 就能回答。

五份模板为原创卡通角色内容（2026-09-20 更新），金木水火土仅为自然意象，不作命理配对。

| ID / 版本 | 名字与形象 | 相处方式 |
| --- | --- | --- |
| metal / 1 | 小铮 · 白金猫头鹰 | 清醒搭档：坦诚、有依据，不说教 |
| wood / 1 | 青芽 · 苔绿小鹿 | 好奇探索：分享发现，不盘问 |
| water / 1 | 澄澄 · 雾蓝水獭 | 温柔倾听：细腻、留白，不读心 |
| fire / 1 | 烁烁 · 珊瑚橙狐狸 | 灵感玩伴：轻巧幽默，知道何时认真 |
| earth / 1 | 团团 · 暖赭小熊 | 安稳陪伴：踏实、不催促，不监督 |

旧目录 gentle/direct/playful/curious 退出新建推荐，不迁移旧实例，也不改写来源 ID。
客户端持有的完整旧快照仍可提交；来源声明不是需要实时解析的外键，也不是权益证明。
各角色具有不同的 traits 数值和对应文字画像/引导，不能只改参数就认为实际表达已改变。
官网通过自身 scripts/sync-companions.mjs 发布这份目录的公开字段快照，不重复维护人格。

每份模板保存默认名字、简短简介、人物身份、完整人格、短句示例和对话偏好
（`preferences`：详略、建议时机、追问习惯）。选中模板即可直接创建，名字和
详细设定都是可选调整。

语音共同要求写在 `persona.modality_notes.voice`：日常 1–3 个短句，一个重点；
不强行追问、不念 Markdown/表情/动作旁白；用户要求详细解释或故事时自然展开。
这些是生成指引，不是字符串硬裁剪，也和 TTS 音色无关。模板同时给出行为指引及
少量对话示例，不能仅靠性格标签保证差异。上述示例为编辑样例，不是模型实测输出。

## 音色不在本阶段

伙伴不携带自己的音色。说话的声音由这台主机的 Channel 配置决定，对所有伙伴
一致，创建和编辑流程里都没有音色选择或试听。这是本阶段刻意收窄的范围：让
“选一个模板就能开始”保持简单。将来若要让伙伴各有各的声音，需要重新设计
可用性协商和会话内绑定失败时的行为，不要从这里的任何字段推断那份设计。

## 竞品依据（2026-09-17 查阅）

- [Replika — What is Replika?](https://help.replika.com/hc/en-us/articles/115001070951-What-is-Replika)：强调倾听、陪伴，允许选择关系方向。
  启发是先让用户理解“怎么相处”，Eidolon 不强制在创建时选择恋爱或亲密关系。
- [Nomi — Getting Started](https://nomi.ai/nomi-knowledge/nomi-101-a-beginners-guide-to-getting-started-with-your-ai-companion/)：基础性格和兴趣作为交流起点，Shared Notes 可进一步塑造人物。
  启发是预设应完整可用，进一步描述应可选。该入门文发表于 2024 年，不能据此认定当前所有 UI 细节。

## Mobile 创建体验

首屏直接选模板创建，名字和详细设定都为可选调整；自定义入口继续保留。
每个模板和自定义分别保留草稿，创建请求发送期间禁止重复提交；结果不确定时
使用相同操作 ID 和完整请求重试。创建成功可直接进入这个伙伴的对话、连接陪伴设备，也可稍后再聊。设备页会引导用户明确选择回应伙伴与可用输出；不自动改绑已有设备。

详细人格文本编辑会更新试聊草稿并使旧回复失效。首次 Workspace 初始化现已接收同一份
persona、preferences 和来源字段，在一个事务内保存首位伙伴；Mobile 共用选角/自定义组件。
只对已认证控制端开放 Owner 建立前的官方模板读取，私人接口仍要求 Owner，首次阶段不提供运行时试聊。
旧客户端未传新增字段时保持默认人格和历史请求指纹兼容。

新增伙伴请求在发出前持久保存完整快照与 operation ID，App 重新进入可继续原请求，
不依赖当前目录。确认成功才清理；本地存储失败不发送创建。首次初始化仍使用 Host 固定
operation 与状态查询，首次未送达草稿尚未跨进程保存。从设备选择器直接创建新伙伴，
以及设备绑定/输出设置的分步恢复，仍待接通。
