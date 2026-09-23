import { Check } from 'lucide-react';

const STEPS = ['Upload', 'Preview', 'Mapping', 'Normalize', 'Validate', 'Ingest'];

interface Props {
    currentStep: number; // 0-indexed
}

// Shows the horizontal progress bar at the top of the wizard
export const StepIndicator = ({ currentStep }: Props) => (
    <div className="step-indicator">
        {STEPS.map((label, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1, minWidth: 0 }}>
                    <div className={`step-circle ${i < currentStep ? 'done' : i === currentStep ? 'active' : ''}`}>
                        {i < currentStep ? <Check size={13} /> : i + 1}
                    </div>
                    <span className={`step-label ${i < currentStep ? 'done' : i === currentStep ? 'active' : ''}`}>
                        {label}
                    </span>
                </div>
                {i < STEPS.length - 1 && (
                    <div className={`step-connector ${i < currentStep ? 'done' : ''}`} />
                )}
            </div>
        ))}
    </div>
);
