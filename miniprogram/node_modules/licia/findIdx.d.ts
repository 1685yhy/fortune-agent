declare function findIdx<T>(
    arr: T[],
    predicate: (val: T, idx: number, arr: T[]) => boolean
): number;

export = findIdx;
