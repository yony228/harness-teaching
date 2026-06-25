// Type declarations for Bun runtime (available at https://bun.sh/docs)
declare module "bun" {
  export const $: {
    (
      strings: TemplateStringsArray,
      ...args: unknown[]
    ): {
      nothrow: () => Promise<unknown>;
      quiet: () => { nothrow: () => Promise<unknown> };
    };
  };
}

export {};
