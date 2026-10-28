import { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useI18n } from '../i18n';
import { ApiError } from '../lib/api';
import {
  decisionErrorKey,
  findingKeys,
  postDecision,
  type DecisionInput,
  type FindingClass,
  type Verdict,
} from '../lib/findings';

const classes: readonly FindingClass[] = [
  'corrosion',
  'crack',
  'leak',
  'deformation',
  'coating',
  'other',
];

export function DecisionForm({ findingId }: { findingId: number }) {
  const { t } = useI18n();
  const id = useId();
  const client = useQueryClient();
  const [comment, setComment] = useState('');
  const [newClass, setNewClass] = useState<FindingClass | ''>('');
  const [validation, setValidation] = useState<'comment' | 'class' | null>(null);
  const mutation = useMutation({
    mutationFn: (input: DecisionInput) => postDecision(findingId, input),
    onSuccess: async () => {
      setComment('');
      setNewClass('');
      await Promise.all([
        client.invalidateQueries({ queryKey: findingKeys.all }),
        client.invalidateQueries({ queryKey: findingKeys.decisions(findingId) }),
      ]);
    },
    retry: false,
  });
  const submit = (verdict: Verdict) => {
    if (mutation.isPending) return;
    mutation.reset();
    if (verdict !== 'confirm' && !comment.trim()) {
      setValidation('comment');
      return;
    }
    if (verdict === 'reclass' && !newClass) {
      setValidation('class');
      return;
    }
    setValidation(null);
    mutation.mutate({
      verdict,
      comment,
      ...(verdict === 'reclass' && newClass ? { newClass } : {}),
    });
  };
  return (
    <form
      className="decision-form"
      aria-label={t('decisions.actions')}
      onSubmit={(event) => event.preventDefault()}
    >
      <div className="qv-field">
        <label htmlFor={`${id}-comment`}>{t('decisions.comment')}</label>
        <textarea
          id={`${id}-comment`}
          className="qv-textarea"
          value={comment}
          maxLength={2000}
          disabled={mutation.isPending}
          onChange={(event) => setComment(event.target.value)}
          aria-invalid={validation === 'comment'}
          aria-describedby={`${id}-comment-hint`}
        />
        <p id={`${id}-comment-hint`} className="qv-caption qv-muted">
          {t('decisions.commentHint')}
        </p>
      </div>
      <div className="qv-field">
        <label htmlFor={`${id}-class`}>{t('decisions.class')}</label>
        <select
          id={`${id}-class`}
          className="qv-select"
          value={newClass}
          disabled={mutation.isPending}
          aria-invalid={validation === 'class'}
          onChange={(event) => setNewClass(event.target.value as FindingClass | '')}
        >
          <option value="">{t('decisions.chooseClass')}</option>
          {classes.map((value) => (
            <option key={value} value={value}>
              {t(`evidence.class.${value}`)}
            </option>
          ))}
        </select>
      </div>
      {validation && (
        <p role="alert" className="qv-caption">
          {t(validation === 'comment' ? 'decisions.commentRequired' : 'decisions.classRequired')}
        </p>
      )}
      {mutation.isError && (
        <p role="alert" className="qv-caption">
          {mutation.error instanceof ApiError && mutation.error.detail
            ? mutation.error.detail
            : t(decisionErrorKey(mutation.error))}
        </p>
      )}
      <div className="qv-actions decision-actions">
        {(['confirm', 'reject', 'reclass'] as const).map((verdict) => (
          <button
            key={verdict}
            type="button"
            className={`qv-btn${verdict === 'confirm' ? '' : ' qv-btn--secondary'}`}
            disabled={mutation.isPending}
            onClick={() => submit(verdict)}
          >
            {t(`decisions.action.${verdict}`)}
          </button>
        ))}
      </div>
      {mutation.isPending && (
        <p role="status" className="qv-caption qv-muted">
          {t('decisions.saving')}
        </p>
      )}
    </form>
  );
}
