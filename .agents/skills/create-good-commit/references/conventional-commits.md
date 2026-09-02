# Conventional Commits Guidance

Read the sections relevant to the message decision. Repository-specific lint rules may narrow these defaults.

## Type Selection

Choose the type from the purpose and observable effect of the change, not from the file extension:

- `feat`: adds user- or consumer-visible capability.
- `fix`: corrects defective behavior.
- `docs`: changes documentation only.
- `refactor`: restructures implementation without intentionally changing behavior.
- `perf`: improves performance without changing the intended behavior.
- `test`: adds or corrects tests without changing production behavior.
- `build`: changes build tooling or dependencies that affect the build.
- `ci`: changes continuous-integration configuration or scripts.
- `style`: changes formatting without changing behavior; do not use it for visual UI styling features.
- `chore`: maintenance that fits no more specific type. Prefer a precise type when one applies.
- `revert`: reverts one or more earlier commits.

When one candidate genuinely needs multiple independent types, split it instead of choosing the most convenient label.

## Scope

Use an optional scope only when it adds stable, useful context. Derive it from repository conventions, package names, subsystems, or established domain terms. Keep the repository's original spelling and do not invent a scope merely to fill the field.

Examples:

```text
feat(auth): 支持短信验证码登录
fix(parser): handle escaped delimiters
docs: 补充本地开发说明
```

## Description and Body

The description is a concise summary after `:`. Do not impose a universal character limit; obey repository lint when present and otherwise keep it easy to scan. Avoid a trailing full stop unless the selected language or repository convention makes it necessary.

Use action-oriented wording natural to the selected language. Do not force English imperative grammar onto other languages.

Add a body after one blank line when future readers need the problem, motivation, behavior change, constraints, or meaningful tradeoffs. Prefer why and impact over a file-by-file account of what the diff already shows.

```text
fix(sync): 避免重复处理已确认的消息

在重连后恢复游标，防止服务端重新投递的消息再次触发业务动作。
```

## Breaking Changes

Mark an incompatible API or behavior change with `!` immediately before `:` or with a `BREAKING CHANGE:` footer. Prefer a footer when migration information is useful.

```text
feat(api)!: 调整用户查询接口的响应结构

BREAKING CHANGE: 用户对象中的 `name` 字段已替换为 `displayName`
```

Keep the token `BREAKING CHANGE` uppercase and in English. Write its value in the selected message language while preserving identifiers.

## Footers and Attribution

Place footers after one blank line following the body. Preserve standard tokens such as `Refs`, `Fixes`, `Co-authored-by`, and `Signed-off-by`.

- Add issue references only when supported by repository context or supplied by the user.
- Add `Co-authored-by` only for actual co-authors and use their provided identity.
- Add `Signed-off-by` only when the user requests it or repository DCO policy requires it. Sign-off certifies provenance; it is not a cryptographic signature.
- Use Git's configured signing behavior or an explicitly requested signing option for cryptographic signatures. Do not fabricate or reconfigure signing identity.

## Reverts

Use `revert` and identify the reverted change in the body or footer when useful:

```text
revert: 撤销短信验证码登录

Refs: 676104e
```

Prefer `git revert` for an already shared commit. Do not rewrite published history merely to improve its message.

## Language Examples

When the user writes in Chinese and does not override the language:

```text
feat(auth): 支持短信验证码登录

增加短信验证码认证流程，方便移动端用户完成登录。

Refs: #128
```

When the same user explicitly requests English:

```text
feat(auth): support SMS verification login

Add an SMS verification flow to simplify authentication on mobile clients.

Refs: #128
```

Translate prose, not syntax. Types, scope identifiers, `BREAKING CHANGE`, trailer tokens, code identifiers, paths, commands, and proper nouns retain their canonical form.
