// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// 手工验收 fixture（非生产插件）：注册 git_commit badge 渲染器。
// vue 经宿主 import map 解析到 /plugins/shared/vue.js（共享宿主实例）。
import { defineComponent, h } from "vue";

const GitCommitBadge = defineComponent({
  props: { evidence: { type: Object, required: true } },
  setup(props) {
    return () => {
      const payload = props.evidence.payload ?? {};
      const sha = String(payload.sha ?? props.evidence.ref ?? "");
      const repo = String(payload.repo ?? "");
      const short = sha.length > 8 ? sha.slice(0, 8) : sha;
      return h("span", { class: "git-commit-badge", title: sha }, [
        h("code", null, repo ? `${repo}@${short}` : short),
      ]);
    };
  },
});

export function activate(api) {
  api.registerBadgeRenderer("git_commit", GitCommitBadge);
}
