import { CSSProperties } from 'react';
/**
 * A constant storing the `prefers-reduced-motion` media query
 * so that when it is turned off, animations are not run.
 */
export declare const euiCanAnimate = "@media screen and (prefers-reduced-motion: no-preference)";
/**
 * A constant storing the `prefers-reduced-motion` media query that will
 * only apply the content if the setting is off (reduce).
 */
export declare const euiCantAnimate = "@media screen and (prefers-reduced-motion: reduce)";
/**
 * Speeds / Durations / Delays
 */
export declare const EuiThemeAnimationSpeeds: readonly ["extraFast", "fast", "normal", "slow", "extraSlow"];
export declare type _EuiThemeAnimationSpeed = (typeof EuiThemeAnimationSpeeds)[number];
export declare type _EuiThemeAnimationSpeeds = {
    /** - Default value: 90ms */
    extraFast: CSSProperties['animationDuration'];
    /** - Default value: 150ms */
    fast: CSSProperties['animationDuration'];
    /** - Default value: 250ms */
    normal: CSSProperties['animationDuration'];
    /** - Default value: 350ms */
    slow: CSSProperties['animationDuration'];
    /** - Default value: 500ms */
    extraSlow: CSSProperties['animationDuration'];
};
/**
 * Easings / Timing functions
 */
export declare const EuiThemeAnimationEasings: readonly ["bounce", "resistance"];
export declare type _EuiThemeAnimationEasing = (typeof EuiThemeAnimationEasings)[number];
export declare type _EuiThemeAnimationEasings = Record<_EuiThemeAnimationEasing, CSSProperties['animationTimingFunction']>;
export declare type _EuiThemeAnimation = _EuiThemeAnimationEasings & _EuiThemeAnimationSpeeds;
