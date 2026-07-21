import types = require('./types');

declare function sortBy<T>(arr: T[], iterator?: types.AnyFn, ctx?: any): T[];

export = sortBy;
