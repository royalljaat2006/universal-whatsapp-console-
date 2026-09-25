"""Backfill the business dimension for rows created before businesses existed.

Every organization that already owns WABAs or contacts gets a "Default"
business; WABAs are assigned to it and the business is propagated down:
WABA -> phone numbers/templates -> conversations -> messages; contacts
take the business of their earliest conversation (else the default).

Assumption (documented in docs/database.md): pre-cycle-1 data is
development-only, so a contact that talked to several WABAs is assigned to
the business of its first conversation rather than split.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Business = apps.get_model("businesses", "Business")
    WABA = apps.get_model("whatsapp", "WhatsAppBusinessAccount")
    Phone = apps.get_model("whatsapp", "WhatsAppPhoneNumber")
    Template = apps.get_model("templates", "MessageTemplate")
    Contact = apps.get_model("contacts", "Contact")
    Identity = apps.get_model("contacts", "ContactIdentity")
    Conversation = apps.get_model("conversations", "Conversation")
    Message = apps.get_model("messaging", "Message")

    for org in Organization.objects.all():
        needs_default = (
            WABA.objects.filter(organization_id=org.pk, business__isnull=True).exists()
            or Contact.objects.filter(organization_id=org.pk, business__isnull=True).exists()
        )
        if not needs_default:
            continue
        default, _ = Business.objects.get_or_create(
            organization_id=org.pk, slug="default", defaults={"name": "Default"}
        )
        WABA.objects.filter(organization_id=org.pk, business__isnull=True).update(business=default)

    for waba in WABA.objects.all():
        Phone.objects.filter(business_account_id=waba.pk).update(business_id=waba.business_id)
        Template.objects.filter(business_account_id=waba.pk).update(business_id=waba.business_id)
    for phone in Phone.objects.all():
        Conversation.objects.filter(phone_number_id=phone.pk).update(business_id=phone.business_id)
    for conversation in Conversation.objects.all():
        Message.objects.filter(conversation_id=conversation.pk).update(
            business_id=conversation.business_id
        )
    for contact in Contact.objects.filter(business__isnull=True):
        first = Conversation.objects.filter(contact_id=contact.pk).order_by("created_at").first()
        business_id = (
            first.business_id
            if first
            else Business.objects.get(organization_id=contact.organization_id, slug="default").pk
        )
        Contact.objects.filter(pk=contact.pk).update(business_id=business_id)
        Identity.objects.filter(contact_id=contact.pk).update(business_id=business_id)


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0001_initial"),
        ("organizations", "0003_membership_business_scope"),
        ("whatsapp", "0002_whatsappbusinessaccount_account_review_status_and_more"),
        ("templates", "0002_messagetemplate_business"),
        ("contacts", "0003_remove_contactidentity_contact_identity_unique_and_more"),
        ("conversations", "0003_remove_conversation_conv_org_phone_last_msg_idx_and_more"),
        ("messaging", "0002_message_business_message_provider_metadata"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
