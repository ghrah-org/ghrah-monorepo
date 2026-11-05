// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

/** import map 产物（`import-map.json`）的最小契约：`{"imports": {specifier: url}}`。 */

export const ImportMapSchema = z
  .object({
    imports: z.record(z.string()).optional().default({}),
  })
  .strict();

export type ImportMap = z.infer<typeof ImportMapSchema>;

export function parseImportMap(raw: unknown): ImportMap {
  return ImportMapSchema.parse(raw);
}
