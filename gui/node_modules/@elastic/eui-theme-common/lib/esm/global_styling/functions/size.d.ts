/**
 * Calculates the `px` value based on a scale multiplier
 * @param scale - The font scale multiplier
 * *
 * @param themeOrBase - Theme base value
 * *
 * @returns string - Rem unit aligned to baseline
 */
export declare const sizeToPixel: (scale?: number) => (themeOrBase: number | {
    [key: string]: any;
    base: number;
}) => string;
