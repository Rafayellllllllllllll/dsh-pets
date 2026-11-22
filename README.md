# dsh-pets

DSH Go 像素宠物的公开归档。每只宠物单独放在 `pets/<id>/`，以后新的宠物也放进这个仓库。

这里只收说明、源图、可复现脚本，以及真正做好的 `pack.json` 和静态 PNG 安装包。没有网站，也不部署。

## 权威格式

像素宠物是一个 zip：`pack.json` 加上它引用的静态 PNG，放在 zip 顶层，或只套一层文件夹。不要用 `pet.json` 作为本仓库的清单。

- 作者指南（包格式）：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/pets/README.md>
- 用户指南（选择宠物、悬浮窗、创意工坊）：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/guide/zh/06-pets.md>
- 上游仓库：<https://github.com/ccch1mneyyy/dsh-Go>

投稿界面以用户指南为准。作者指南第 9 节仍写论坛登录和先审后发，与现行用户指南不一致。制作和检查见 [docs/spec.md](docs/spec.md)。

## 目录

```text
pets/<id>/
  README.md          名称、状态、来源说明
  source/            源图。未完成的宠物可以只有这里
  scripts/           可复现脚本。有脚本就提交，并写明命令
  pack.json          清单。包未完成时不要放
  *.png              静态图集
  <id>.zip           安装包。尚未验证前不要放
```

`id` 使用小写字母、数字和连字符，例如 `qianxia`。

## 现在有哪些宠物

| 目录 | 名称 | 状态 |
| --- | --- | --- |
| [pets/qianxia](pets/qianxia/) | 千夏像素宠物 | 只有 4×4 源图。尚不可导入 |

## 结构检查

```sh
sh scripts/check-repo.sh
```

这个脚本只检查目录、源图是否原样保留，以及未完成宠物有没有被写成可导入。它不验证宠物包，也不能代替手机 App 里的导入。

## 许可

本仓库不设统一开源许可证，也不对第三方角色整体声明 MIT、CC 或其他开源许可。某只宠物的说明里如果另行写了许可，只覆盖那一份说明本身。
