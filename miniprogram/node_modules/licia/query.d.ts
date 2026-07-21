declare const query: {
    parse(str: string): Record<string, string | string[]>;
    stringify(object: any): string;
};

export = query;
