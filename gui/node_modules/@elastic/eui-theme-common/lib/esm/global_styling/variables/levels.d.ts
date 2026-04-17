import { CSSProperties } from 'react';
/**
 * Z-Index
 *
 * Remember that z-index is relative to parent and based on the stacking context.
 * z-indexes only compete against other z-indexes when they exist as children of
 * that shared parent.
 *
 * That means a popover with a settings of 2, will still show above a modal
 * with a setting of 100, if it is within that modal and not besides it.
 *
 * Generally that means it's a good idea to consider things added to this file
 * as competitive only as siblings.
 *
 * https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_Positioning/Understanding_z_index/The_stacking_context
 */
export declare const EuiThemeLevels: readonly ["toast", "modal", "mask", "navigation", "menu", "header", "flyout", "maskBelowHeader", "content"];
export declare type _EuiThemeLevel = (typeof EuiThemeLevels)[number];
export declare type _EuiThemeLevels = {
    /** - Default value: 9000 */
    toast: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 8000 */
    modal: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 6000 */
    mask: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 6000 */
    navigation: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 2000 */
    menu: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 1000 */
    header: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 1000 */
    flyout: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 1000 */
    maskBelowHeader: NonNullable<CSSProperties['zIndex']>;
    /** - Default value: 0 */
    content: NonNullable<CSSProperties['zIndex']>;
};
