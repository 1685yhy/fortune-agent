declare const base64: {
    encode(bytes: number[] | Uint8Array): string;
    decode(str: string): number[];
};

export = base64;
