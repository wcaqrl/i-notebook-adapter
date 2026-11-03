# i-notebook 懒猫应用自动更新适配器

为现有应用 **`com.luck.lazycat.notebook`** 提供更新，保留原包名、`notebook` 服务、应用子域名和数据库文件。上游项目保持独立；本仓库复用 [`lazycat-action@v1`](https://github.com/wcaqrl/lazycat-action)。

## 版本来源与发布过程

- 权威版本源：[Docker Hub `aidenzou/i-notebook`](https://hub.docker.com/r/aidenzou/i-notebook/tags)。仅比较完整 `x.y.z` 正式标签的数字版本，不使用 `latest`、测试版或镜像更新时间判断版本。
- 平台：首期 `linux/amd64`；不因上游还有 arm64 就直接宣称旧应用支持该架构。
- 商店旧包版本为 **0.0.1**，嵌入镜像实际对应上游 **0.7.1**；首次升级候选为分析时的 **0.17.1**，运行时会重新查询正式版本。
- 中文日志来自[作者更新日志](https://notebook.xiguabyte.com/s/cme25e18g0003qp01q8np7oix)，仅提取目标版本节；缺失时停止，不把整个历史或其它版本当作本次日志。商店处理缺失语言的翻译。

```text
GitHub 定时/手动触发 → 选择正式镜像版本及 amd64 摘要
  → 提取该版本中文更新日志
  → Runner 验证旧库升级、全新安装、读写与重建
  → 官方 copy-image，等待 finished=true 并采用返回的真实镜像地址
  → 校验测试摘要和交付摘要一致，生成带版本号的 .lpk
  → 并行：提交商店审核 / 发布 GitHub Release 的 .lpk 附件
  → 提交配置与 .lazycat-action.lock.yml 状态
```

Release 失败不阻塞送审；不上传 `.sha256` 附件。GitHub 自动显示的 Source code 压缩包由 GitHub 提供。锁文件记录处理及审核结果，同一被拒绝源版本不会每日重复送审；新上游版本可以替代旧待审核版本。修复拒审问题后才能显式选择 `retry-rejected`。

## Actions 配置与操作

1. Settings → Secrets and variables → Actions → **Secrets**：设置 `LZC_API_TOKEN`，PAT 所属开发者需有此应用的所有者或协作者权限。无需 Docker 登录、GitHub 上游 Token 或 SSH Key。
2. 工作流为 **Update i-notebook for LazyCat**。每次适配代码 push 会先在 GitHub Runner 做真实容器测试，再只读检查版本，不送审。
3. Actions → Run workflow：默认勾选 `dry-run`，只检查版本和日志；取消勾选后验证权限、运行升级测试、转存、打包并送审。
4. 每天北京时间 **13:23** 定时触发，GitHub 调度可能延迟。设置 Actions **Variables** 的 `LAZYCAT_AUTO_PUBLISH=true` 才会自动真实发布；未设置时定时仅检查。
5. `contents: write` 用于记录状态和发布 Release。不要忽略、删除或伪造 `.lazycat-action.lock.yml`。`dist/`、测试资料、临时凭据和 `.lpk` 不提交 Git。

## 数据与旧版升级

| 内容 | 微服应用持久目录 | 新容器路径 |
|---|---|---|
| 数据库、迁移备份 | `/lzcapp/var/data/db.sqlite` 等 | `/appdata` |
| 上传附件 | `/lzcapp/var/data/uploads` | `/app/uploads` |

旧包将 `/lzcapp/var/data` 挂到 `/app/prisma`，新版改为 `/appdata`，但 **宿主机目录和 `db.sqlite` 文件名完全保留**。使用新镜像自己的初始化/迁移命令；不再把旧种子数据库复制到新安装中，不覆盖已有数据库。`SITE_URL` 使用 `https://{{ .S.AppDomain }}`。

保留原来的懒猫登录验证，没有配置 `public_path` 或共享的固定解锁密码。应用仍是单实例共享知识库；本适配器没有增加用户之间的笔记隔离。开放 API 默认遵循上游设置，测试用 API Key 仅在 Runner 临时数据中生成，不会打包。

### 旧数据库迁移兼容

实际测试发现：上游 0.17.1 对旧 Prisma 库重建 `Note` 表时，在事务内切换 SQLite 外键开关无效，导致子笔记 `pId` 被 `ON DELETE SET NULL` 清空。本包通过 `content/prepare-runtime.mjs` 在原启动命令前修正已知迁移代码，将外键开关移到旧迁移事务外，并检查迁移后的外键完整性。修正仅写容器临时文件系统，不改数据库内容或覆盖原库；数据库仍由上游执行迁移和备份。无法识别未来上游迁移实现时会停止验证，要求维护者复查。

上游新版还移除了旧 `Task` 看板功能。适配器会在迁移前将非空旧任务的全部字段导出至 `/lzcapp/var/data/legacy-task-exports/tasks-<内容摘要>.json`，并保留上游完整旧库备份。**旧任务不会自动转换成新版多维集合，也不能直接在新版界面中编辑**；需要任务看板的用户应先导出并评估迁移后再升级。目录层级、正文、历史、收藏和附件会单独验证。

### 旧附件必须在更新前导出

**旧商店 0.0.1 没有持久化 `/app/public/uploads`。** 尚在旧容器中的附件无法由新包凭空恢复；升级/重建前先停止编辑，导出旧数据库和附件。下列路径是在服务容器内看到的应用目录，不能当作微服宿主机上的绝对路径使用。

在微服宿主机找出当前应用的服务容器，将 `OLD_CONTAINER` 替换为其实际名称或 ID，再备份到自己的可写目录：

```sh
lzc-docker ps --format '{{.ID}} {{.Names}}' | grep 'com.luck.lazycat.notebook'
OLD_CONTAINER='替换为旧 notebook 服务容器 ID'
BACKUP_DIR='/替换为你自己的可写备份目录/i-notebook-before-upgrade'
mkdir -p "$BACKUP_DIR"
lzc-docker stop "$OLD_CONTAINER"
lzc-docker cp "$OLD_CONTAINER:/app/prisma/." "$BACKUP_DIR/"
mkdir -p "$BACKUP_DIR/uploads"
lzc-docker cp "$OLD_CONTAINER:/app/public/uploads/." "$BACKUP_DIR/uploads/"
```

确认数据库与附件副本完整后，把附件合并复制到旧容器持久化数据库目录的 `uploads` 子目录（不要移动或删除唯一备份）：

```sh
lzc-docker cp "$BACKUP_DIR/uploads" "$OLD_CONTAINER:/app/prisma/"
lzc-docker start "$OLD_CONTAINER"
```

新版会挂载该 `uploads` 子目录，并通过懒猫 `/uploads/ → /api/file/` 路由保留原笔记图片链接。导出失败时先排查旧容器是否仍有原文件；不要为此删除数据库或卸载旧应用。首次升级也可在备份完成后再通过微服应用管理停止旧应用。升级涉及数据库结构变化，回退时需使用升级前数据库与附件副本，不能只换回旧镜像。

## 验证内容

`scripts/test-upgrade.sh` 在 Runner 使用隔离的临时目录：

- 校验原商店 LPK SHA256，使用真实旧 schema，植入中文父子笔记、历史和设置；启动固定摘要的上游 0.7.1，实际上传/下载图片。
- 用同一数据库和附件目录切换到选中版本的固定摘要，验证中文正文、层级、历史、鉴权、API 创建/读取笔记、图片下载、SQLite 完整性与迁移备份。
- 重建容器，再次验证；另外从空目录测试初始化、新笔记及附件写入和重建。
- 全部通过才生成临时升级证据，LPK buildscript 再对比最终交付源摘要及兼容脚本 SHA256。

本地可运行轻量检查，不启动容器：

```sh
python3 -B -m unittest discover -s tests -v
bash -n scripts/test-upgrade.sh
LAZYCAT_SOURCE_VERSION=0.17.1 python3 -B scripts/upstream.py changelog
```

容器测试在 GitHub Runner 完成，不访问或安装到开发者的微服生产实例。路由声明依据[懒猫路由文档](https://developer.lazycat.cloud/advanced-route.html)；实际微服安装后的外部路由仍应由使用者验收。

图标直接取自上游 0.17.1 镜像中的 `public/icon-512.png`（512×512 PNG），没有使用占位图。
