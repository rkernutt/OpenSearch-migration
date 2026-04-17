import { useState } from "react";
import {
  EuiTitle,
  EuiSpacer,
  EuiButton,
  EuiButtonEmpty,
  EuiFlexGroup,
  EuiFlexItem,
  EuiText,
  EuiCallOut,
  EuiPanel,
  EuiFieldSearch,
  EuiCheckbox,
  EuiHorizontalRule,
  EuiBadge,
  EuiFormRow,
  EuiFieldNumber,
  EuiSelect,
  EuiLoadingSpinner,
  EuiSplitPanel,
  EuiIcon,
} from "@elastic/eui";

export interface IndexInfo {
  name: string;
  docCount: number;
  sizeBytes: number;
}

interface IndicesPageProps {
  availableIndices: IndexInfo[];
  selectedIndices: string[];
  indicesLoading: boolean;
  indicesError: string;
  batchSize: number;
  slices: string;
  onLoadIndices: () => void;
  onToggleIndex: (name: string) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onBatchSizeChange: (v: number) => void;
  onSlicesChange: (v: string) => void;
  onBack: () => void;
  onNext: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const SLICES_OPTIONS = [
  { value: "auto", text: "auto (recommended)" },
  { value: "1", text: "1" },
  { value: "2", text: "2" },
  { value: "4", text: "4" },
  { value: "8", text: "8" },
];

export function IndicesPage({
  availableIndices,
  selectedIndices,
  indicesLoading,
  indicesError,
  batchSize,
  slices,
  onLoadIndices,
  onToggleIndex,
  onSelectAll,
  onClearAll,
  onBatchSizeChange,
  onSlicesChange,
  onBack,
  onNext,
}: IndicesPageProps) {
  const [filter, setFilter] = useState("");

  const visible = availableIndices.filter((idx) =>
    idx.name.toLowerCase().includes(filter.toLowerCase()),
  );

  const totalSelectedDocs = availableIndices
    .filter((i) => selectedIndices.includes(i.name))
    .reduce((sum, i) => sum + i.docCount, 0);

  const totalSelectedBytes = availableIndices
    .filter((i) => selectedIndices.includes(i.name))
    .reduce((sum, i) => sum + i.sizeBytes, 0);

  const canNext = selectedIndices.length > 0;

  return (
    <>
      <EuiTitle size="s">
        <h2>Select Indices</h2>
      </EuiTitle>
      <EuiText color="subdued" size="s">
        <p>Choose which indices to migrate and configure reindex parameters.</p>
      </EuiText>
      <EuiSpacer size="l" />

      {/* Load indices */}
      <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton
            iconType="refresh"
            onClick={onLoadIndices}
            isLoading={indicesLoading}
            isDisabled={indicesLoading}
          >
            {availableIndices.length === 0 ? "Load Indices from Source" : "Refresh Indices"}
          </EuiButton>
        </EuiFlexItem>
        {availableIndices.length > 0 && (
          <>
            <EuiFlexItem grow={false}>
              <EuiButtonEmpty size="s" onClick={onSelectAll}>
                Select All
              </EuiButtonEmpty>
            </EuiFlexItem>
            <EuiFlexItem grow={false}>
              <EuiButtonEmpty size="s" onClick={onClearAll}>
                Clear All
              </EuiButtonEmpty>
            </EuiFlexItem>
          </>
        )}
      </EuiFlexGroup>

      {indicesError && (
        <>
          <EuiSpacer size="m" />
          <EuiCallOut title="Could not load indices" color="warning" iconType="warning" size="s">
            <p>
              {indicesError} — You can enter index names manually below, or ensure network access to
              the source cluster.
            </p>
          </EuiCallOut>
        </>
      )}

      {indicesLoading && (
        <>
          <EuiSpacer size="m" />
          <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
            <EuiFlexItem grow={false}>
              <EuiLoadingSpinner size="m" />
            </EuiFlexItem>
            <EuiFlexItem grow={false}>
              <EuiText size="s" color="subdued">
                <p>Loading indices from source cluster…</p>
              </EuiText>
            </EuiFlexItem>
          </EuiFlexGroup>
        </>
      )}

      {availableIndices.length > 0 && (
        <>
          <EuiSpacer size="m" />
          <EuiSplitPanel.Outer hasBorder>
            <EuiSplitPanel.Inner paddingSize="s" color="subdued">
              <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
                <EuiFlexItem>
                  <EuiFieldSearch
                    placeholder="Filter indices…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    compressed
                  />
                </EuiFlexItem>
                <EuiFlexItem grow={false}>
                  <EuiText size="xs" color="subdued">
                    <span>
                      {selectedIndices.length} / {availableIndices.length} selected
                    </span>
                  </EuiText>
                </EuiFlexItem>
              </EuiFlexGroup>
            </EuiSplitPanel.Inner>
            <EuiSplitPanel.Inner paddingSize="none" style={{ maxHeight: 320, overflowY: "auto" }}>
              {visible.map((idx, i) => (
                <div key={idx.name}>
                  {i > 0 && <EuiHorizontalRule margin="none" />}
                  <div
                    style={{ padding: "10px 16px", cursor: "pointer" }}
                    onClick={() => onToggleIndex(idx.name)}
                  >
                    <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
                      <EuiFlexItem grow={false}>
                        <EuiCheckbox
                          id={`idx-${idx.name}`}
                          checked={selectedIndices.includes(idx.name)}
                          onChange={() => onToggleIndex(idx.name)}
                          label=""
                        />
                      </EuiFlexItem>
                      <EuiFlexItem>
                        <EuiText size="s">
                          <strong>{idx.name}</strong>
                        </EuiText>
                      </EuiFlexItem>
                      <EuiFlexItem grow={false}>
                        <EuiFlexGroup gutterSize="s" responsive={false}>
                          <EuiFlexItem grow={false}>
                            <EuiBadge color="hollow">
                              <EuiIcon type="documents" size="s" /> {formatCount(idx.docCount)}
                            </EuiBadge>
                          </EuiFlexItem>
                          <EuiFlexItem grow={false}>
                            <EuiBadge color="hollow">{formatBytes(idx.sizeBytes)}</EuiBadge>
                          </EuiFlexItem>
                        </EuiFlexGroup>
                      </EuiFlexItem>
                    </EuiFlexGroup>
                  </div>
                </div>
              ))}
              {visible.length === 0 && (
                <div style={{ padding: 16 }}>
                  <EuiText size="s" color="subdued" textAlign="center">
                    <p>No indices match "{filter}"</p>
                  </EuiText>
                </div>
              )}
            </EuiSplitPanel.Inner>
          </EuiSplitPanel.Outer>

          {selectedIndices.length > 0 && (
            <>
              <EuiSpacer size="s" />
              <EuiText size="xs" color="subdued">
                <span>
                  Selected: {formatCount(totalSelectedDocs)} docs ·{" "}
                  {formatBytes(totalSelectedBytes)}
                </span>
              </EuiText>
            </>
          )}
        </>
      )}

      <EuiSpacer size="xl" />

      {/* Reindex parameters */}
      <EuiPanel hasBorder paddingSize="l">
        <EuiTitle size="xs">
          <h3>Reindex Parameters</h3>
        </EuiTitle>
        <EuiSpacer size="m" />
        <EuiFlexGroup gutterSize="l" responsive={false}>
          <EuiFlexItem style={{ maxWidth: 220 }}>
            <EuiFormRow label="Batch Size" helpText="Documents per scroll page (scroll_size)">
              <EuiFieldNumber
                value={batchSize}
                onChange={(e) => onBatchSizeChange(Number(e.target.value))}
                min={100}
                max={10000}
                step={100}
              />
            </EuiFormRow>
          </EuiFlexItem>
          <EuiFlexItem style={{ maxWidth: 220 }}>
            <EuiFormRow label="Slices" helpText="Parallel reindex slices (auto = shard count)">
              <EuiSelect
                options={SLICES_OPTIONS}
                value={slices}
                onChange={(e) => onSlicesChange(e.target.value)}
              />
            </EuiFormRow>
          </EuiFlexItem>
        </EuiFlexGroup>
      </EuiPanel>

      <EuiSpacer size="l" />

      <EuiFlexGroup gutterSize="m" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton iconType="arrowLeft" onClick={onBack}>
            Back
          </EuiButton>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiButton
            fill
            iconType="arrowRight"
            iconSide="right"
            onClick={onNext}
            isDisabled={!canNext}
          >
            Next: Execute
          </EuiButton>
        </EuiFlexItem>
      </EuiFlexGroup>
    </>
  );
}
