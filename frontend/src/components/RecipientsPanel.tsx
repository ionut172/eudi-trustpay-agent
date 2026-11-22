import { ArrowUpRight } from 'lucide-react'

interface RecipientsPanelProps {
  onSelect: (name: string) => void
}

const recipients = [
  { name: 'Alice Visser', initials: 'AV' },
  { name: 'George Visser', initials: 'GV' },
  { name: 'Ionut Gabriel', initials: 'IG' },
]

export function RecipientsPanel({ onSelect }: RecipientsPanelProps) {
  return (
    <section className="mobile-contacts" aria-label="Saved recipients">
      <div className="mobile-contacts-heading">
        <h2>Pay a contact</h2>
        <span>Demo contacts</span>
      </div>
      <div className="mobile-contact-list">
        {recipients.map((recipient) => (
          <button
            className="mobile-contact"
            key={recipient.name}
            onClick={() => onSelect(recipient.name)}
            aria-label={`Start a payment to ${recipient.name}`}
          >
            <span className="mobile-contact-avatar">{recipient.initials}</span>
            <span>{recipient.name.split(' ')[0]}</span>
            <ArrowUpRight size={13} aria-hidden="true" />
          </button>
        ))}
      </div>
      <p className="mobile-contacts-note">Sample contacts only — not connected to a bank address book.</p>
    </section>
  )
}
