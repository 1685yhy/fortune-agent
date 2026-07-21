import Emitter = require('./Emitter');
import each = require('./each');

declare class Store extends Emitter {
    constructor(data?: {});
    set<T>(key: string, val: T): void;
    set(values: {}): void;
    get<T = any>(key: string): T;
    get(keys: string[]): {};
    remove(key: string): void;
    remove(keys: string[]): void;
    clear(): void;
    each(fn: (...args: any[]) => void): void;
}

export = Store;
