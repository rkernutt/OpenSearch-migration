import React from "react";
import {
  EuiPageTemplate,
  EuiIcon,
  EuiBadge,
  EuiFlexGroup,
  EuiFlexItem,
  EuiSpacer,
  EuiStepsHorizontal,
  EuiHeader,
  EuiHeaderSectionItem,
  EuiTitle,
} from "@elastic/eui";
import { ElasticMark, OpenSearchMark, MigrationArrow } from "./Logo";

interface AppLayoutProps {
  activePage: string;
  onNavigate: (page: string) => void;
  children: React.ReactNode;
  sourceConnected: boolean;
  targetConnected: boolean;
  methodSelected: boolean;
  indicesSelected: boolean;
}

const STEPS = [
  { id: "source", title: "Source" },
  { id: "target", title: "Target" },
  { id: "method", title: "Method" },
  { id: "indices", title: "Indices" },
  { id: "execute", title: "Execute" },
] as const;

const STEP_IDS = STEPS.map((s) => s.id);

export function AppLayout({
  activePage,
  onNavigate,
  children,
  sourceConnected,
  targetConnected,
  methodSelected,
  indicesSelected,
}: AppLayoutProps) {
  const activeStepIdx = STEP_IDS.indexOf(activePage as (typeof STEP_IDS)[number]);

  const stepStatuses = STEPS.map((step, idx) => {
    const isPast = activeStepIdx === -1 || idx < activeStepIdx;
    let isComplete = false;
    if (isPast) {
      if (step.id === "source") isComplete = sourceConnected;
      if (step.id === "target") isComplete = targetConnected;
      if (step.id === "method") isComplete = methodSelected;
      if (step.id === "indices") isComplete = indicesSelected;
    }

    let stepStatus: "complete" | "current" | "incomplete" | "disabled";
    if (idx === activeStepIdx) {
      stepStatus = "current";
    } else if (isComplete) {
      stepStatus = "complete";
    } else {
      stepStatus = "incomplete";
    }

    return {
      title: step.title,
      status: stepStatus,
      onClick: () => onNavigate(step.id),
    };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      {/* ── Dark header bar ─────────────────────────────────────────── */}
      <EuiHeader
        theme="dark"
        position="fixed"
        sections={[
          {
            items: [
              <EuiHeaderSectionItem key="brand">
                <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
                  {/* OpenSearch mark */}
                  <EuiFlexItem grow={false}>
                    <OpenSearchMark height={28} />
                  </EuiFlexItem>

                  {/* Migration arrow */}
                  <EuiFlexItem grow={false}>
                    <MigrationArrow height={22} />
                  </EuiFlexItem>

                  {/* Elastic mark */}
                  <EuiFlexItem grow={false}>
                    <ElasticMark height={28} />
                  </EuiFlexItem>

                  {/* App title */}
                  <EuiFlexItem grow={false}>
                    <EuiTitle size="s">
                      <h1
                        style={{
                          color: "#fff",
                          fontWeight: 700,
                          letterSpacing: "-0.02em",
                          whiteSpace: "nowrap",
                          marginLeft: 8,
                        }}
                      >
                        Migration Assistant
                      </h1>
                    </EuiTitle>
                  </EuiFlexItem>
                </EuiFlexGroup>
              </EuiHeaderSectionItem>,
            ],
          },
          {
            items: [
              <EuiHeaderSectionItem key="badges">
                <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
                  {sourceConnected && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color="success">
                        <EuiIcon type="check" size="s" /> Source
                      </EuiBadge>
                    </EuiFlexItem>
                  )}
                  {targetConnected && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color="success">
                        <EuiIcon type="check" size="s" /> Target
                      </EuiBadge>
                    </EuiFlexItem>
                  )}
                  <EuiFlexItem grow={false}>
                    <EuiBadge color="hollow">v1.0.0</EuiBadge>
                  </EuiFlexItem>
                </EuiFlexGroup>
              </EuiHeaderSectionItem>,
            ],
          },
        ]}
      />

      {/* ── Main content area ────────────────────────────────────────── */}
      <EuiPageTemplate restrictWidth={1200} grow style={{ paddingTop: 48 }}>
        <EuiPageTemplate.Section>
          {/* Wizard stepper */}
          <EuiStepsHorizontal steps={stepStatuses} />
          <EuiSpacer size="l" />
          {children}
        </EuiPageTemplate.Section>
      </EuiPageTemplate>
    </div>
  );
}
