// Stylelint：只管“对不对”，不管“好不好看”。
//
// 排版交给 prettier（它的范围覆盖 CSS/SFC），因此这里关闭标准配置里的纯格式类规则，
// 避免两个格式化器互相覆盖、也避免为了通过检查而改动既有视觉。保留的是正确性规则：
// 未知属性/单位、重复声明、无效位置、简写冗余等。
//
// 与 CSS 治理方案的关系：本文件只负责“存量代码不能更差”的第一道门；裸色值、px 字号、
// 任意值 utility、palette 类等治理规则需要 ratchet 基线，后续由 check-style-governance 承担。
export default {
  extends: ["stylelint-config-standard", "stylelint-config-recommended-vue"],
  ignoreFiles: ["**/dist/**", "**/node_modules/**", "**/public/**", "**/coverage/**"],
  rules: {
    // 存量命名是 BEM 与历史类名混用，且 CSS 自定义属性沿用 --font-ui-* 之类的短横线风格
    "selector-class-pattern": null,
    "custom-property-pattern": null,
    // 迁移期 style.css 里存在依赖顺序的历史写法，先不阻断，留待阶段 3 清理
    "no-descending-specificity": null,
    // Vue scoped 的 :deep() 与 :global() 不是未知伪类
    "selector-pseudo-class-no-unknown": [true, { ignorePseudoClasses: ["deep", "global"] }],

    // ---- 交给 prettier 的格式类规则 ----
    "rule-empty-line-before": null,
    "comment-empty-line-before": null,
    "at-rule-empty-line-before": null,
    "declaration-empty-line-before": null,
    "color-hex-length": null,
    "color-function-notation": null,
    "color-function-alias-notation": null,
    "alpha-value-notation": null,
    "media-feature-range-notation": null,
    "value-keyword-case": null,
    "number-max-precision": null,

    // ---- 存量兼容 ----
    // -webkit-text-size-adjust 仍用于旧版 Safari/iOS（该规则按带前缀的属性名匹配）
    "property-no-vendor-prefix": [true, { ignoreProperties: ["-webkit-text-size-adjust"] }],
    // word-break: break-word 已废弃但兼容性最好，替换成 overflow-wrap 会改变换行行为
    "declaration-property-value-keyword-no-deprecated": null,
  },
};
