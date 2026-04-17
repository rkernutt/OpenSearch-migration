/**
 * Like `keyof typeof`, but for getting values instead of keys
 * ValueOf<typeof {key1: 'value1', key2: 'value2'}>
 * Results in `'value1' | 'value2'`
 */
export declare type ValueOf<T> = T[keyof T];
/**
 * Replaces all properties on any type as optional, includes nested types
 *
 * @example
 * ```ts
 * interface Person {
 *  name: string;
 *  age?: number;
 *  spouse: Person;
 *  children: Person[];
 * }
 * type PartialPerson = RecursivePartial<Person>;
 * // results in
 * interface PartialPerson {
 *  name?: string;
 *  age?: number;
 *  spouse?: RecursivePartial<Person>;
 *  children?: RecursivePartial<Person>[]
 * }
 * ```
 */
export declare type RecursivePartial<T> = {
    [P in keyof T]?: T[P] extends NonAny[] ? T[P] : T[P] extends readonly NonAny[] ? T[P] : T[P] extends Array<infer U> ? Array<RecursivePartial<U>> : T[P] extends ReadonlyArray<infer U> ? ReadonlyArray<RecursivePartial<U>> : T[P] extends Set<infer V> ? Set<RecursivePartial<V>> : T[P] extends Map<infer K, infer V> ? Map<K, RecursivePartial<V>> : T[P] extends NonAny ? T[P] : RecursivePartial<T[P]>;
};
declare type NonAny = number | boolean | string | symbol | null;
export {};
