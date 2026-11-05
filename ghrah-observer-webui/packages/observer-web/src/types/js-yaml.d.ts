declare module "js-yaml" {
  export interface DumpOptions {
    indent?: number;
    lineWidth?: number;
    noRefs?: boolean;
    quotingType?: "'" | '"';
  }

  export interface LoadOptions {
    json?: boolean;
  }

  export function dump(value: unknown, options?: DumpOptions): string;
  export function load(source: string, options?: LoadOptions): unknown;
}
