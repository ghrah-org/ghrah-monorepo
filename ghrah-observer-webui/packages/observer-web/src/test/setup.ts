// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { config } from "@vue/test-utils";
import { i18n, setLocale } from "../i18n";

// happy-dom 未实现 window.confirm；Vitest 5 不再允许 spy 未定义属性。
if (typeof window !== "undefined" && typeof window.confirm !== "function") {
  window.confirm = () => true;
}

config.global.plugins = [i18n];
setLocale("en");
