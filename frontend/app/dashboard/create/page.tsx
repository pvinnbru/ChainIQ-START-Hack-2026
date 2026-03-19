'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { toast } from 'sonner';
import { useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Paperclip } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';

const CATEGORIES: Record<string, string[]> = {
  IT: [
    'Laptops', 'Mobile Workstations', 'Desktop Workstations', 'Monitors',
    'Docking Stations', 'Smartphones', 'Tablets', 'Rugged Devices',
    'Accessories Bundles', 'Replacement / Break-Fix Pool Devices',
    'Cloud Compute', 'Cloud Storage', 'Cloud Networking',
    'Managed Cloud Platform Services', 'Cloud Security Services',
  ],
  Facilities: [
    'Workstations and Desks', 'Office Chairs', 'Meeting Room Furniture',
    'Storage Cabinets', 'Reception and Lounge Furniture',
  ],
  'Professional Services': [
    'Cloud Architecture Consulting', 'Cybersecurity Advisory',
    'Data Engineering Services', 'Software Development Services',
    'IT Project Management Services',
  ],
  Marketing: [
    'Search Engine Marketing (SEM)', 'Social Media Advertising',
    'Content Production Services', 'Marketing Analytics Services',
    'Influencer Campaign Management',
  ],
};

const UNIT_OPTIONS = ['units', 'consulting_day', 'device', 'set', 'license', 'TB_month', 'instance_hour', 'seat_license', 'campaign', 'project'];
const CONTRACT_OPTIONS = ['purchase', 'service', 'framework'];
const CURRENCIES = ['EUR', 'CHF', 'USD'];

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function CreateRequestPage() {
  const { user } = useAuth();
  const router = useRouter();

  const [plainText, setPlainText] = useState('');
  const [title, setTitle] = useState('');
  const [categoryL1, setCategoryL1] = useState('');
  const [categoryL2, setCategoryL2] = useState('');
  const [currency, setCurrency] = useState('EUR');
  const [budgetAmount, setBudgetAmount] = useState('');
  const [quantity, setQuantity] = useState('');
  const [unitOfMeasure, setUnitOfMeasure] = useState('');
  const [requiredByDate, setRequiredByDate] = useState('');
  const [preferredSupplier, setPreferredSupplier] = useState('');
  const [incumbentSupplier, setIncumbentSupplier] = useState('');
  const [contractType, setContractType] = useState('');
  const [deliveryCountries, setDeliveryCountries] = useState(user?.country ?? '');
  const [dataResidency, setDataResidency] = useState(false);
  const [esgRequirement, setEsgRequirement] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const parseAndFill = (file: File) => {
    if (!file.name.endsWith('.json') && file.type !== 'application/json') {
      toast.error('Please drop a .json file.');
      return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const raw = JSON.parse(ev.target?.result as string);
        const d = raw.global_context_snapshot ?? raw;
        if (d.plain_text) setPlainText(d.plain_text);
        if (d.title) setTitle(d.title);
        if (d.category_l1) setCategoryL1(d.category_l1);
        if (d.category_l2) setCategoryL2(d.category_l2);
        if (d.currency) setCurrency(d.currency);
        const budget = d.budget_amount ?? d.budget;
        if (budget != null) setBudgetAmount(String(budget));
        if (d.quantity != null) setQuantity(String(d.quantity));
        if (d.unit_of_measure) setUnitOfMeasure(d.unit_of_measure);
        else if (d.amount_unit) setUnitOfMeasure(d.amount_unit);
        if (d.required_by_date) setRequiredByDate(d.required_by_date);
        if (d.preferred_supplier_mentioned) setPreferredSupplier(d.preferred_supplier_mentioned);
        if (d.incumbent_supplier) setIncumbentSupplier(d.incumbent_supplier);
        if (d.contract_type_requested) setContractType(d.contract_type_requested);
        const countries = d.delivery_countries ?? d.delivery_country;
        if (countries) setDeliveryCountries(Array.isArray(countries) ? countries.join(', ') : countries);
        if (d.data_residency_constraint != null) setDataResidency(!!d.data_residency_constraint);
        if (d.esg_requirement != null) setEsgRequirement(!!d.esg_requirement);
        toast.success('JSON imported — review and submit.');
      } catch {
        toast.error('Invalid JSON file.');
      }
    };
    reader.readAsText(file);
  };

  const handleJsonImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) parseAndFill(file);
    e.target.value = '';
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) parseAndFill(file);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!plainText.trim()) {
      toast.error('Please describe your request.');
      return;
    }

    setSubmitting(true);
    try {
      const payload = {
        plain_text: plainText,
        title: title || plainText.slice(0, 80),
        category_l1: categoryL1 || undefined,
        category_l2: categoryL2 || undefined,
        currency: currency || undefined,
        budget_amount: budgetAmount ? parseFloat(budgetAmount) : undefined,
        quantity: quantity ? parseFloat(quantity) : undefined,
        unit_of_measure: unitOfMeasure || undefined,
        required_by_date: requiredByDate || undefined,
        preferred_supplier_mentioned: preferredSupplier || undefined,
        incumbent_supplier: incumbentSupplier || undefined,
        contract_type_requested: contractType || undefined,
        delivery_countries: deliveryCountries
          ? deliveryCountries.split(',').map((c) => c.trim()).filter(Boolean)
          : undefined,
        data_residency_constraint: dataResidency,
        esg_requirement: esgRequirement,
      };

      const res = await fetch(`${API}/requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error('Failed to submit request');
      toast.success('Request submitted successfully!');
      router.push('/dashboard/cases');
    } catch {
      toast.error('Failed to submit request. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const l2Options = categoryL1 ? CATEGORIES[categoryL1] ?? [] : [];

  return (
    <div className="py-8 w-full max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">New Request</h1>
        <p className="text-muted-foreground mt-1">
          Describe what you need — our AI will handle the rest.
        </p>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={handleJsonImport}
      />

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Plain-text description */}
        <Card
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          className={dragging ? 'ring-2 ring-primary ring-offset-2 transition-all' : ''}
        >
          <CardHeader className="flex flex-row items-start justify-between pb-2">
            <div>
              <CardTitle className="text-base">What do you need?</CardTitle>
              <CardDescription className="mt-1">
                Describe your request in plain language. Be as specific as possible.
              </CardDescription>
            </div>
            <button
              type="button"
              title="Upload JSON"
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <Paperclip className="w-4 h-4" />
            </button>
          </CardHeader>
          <CardContent>
            <Textarea
              placeholder={dragging ? 'Drop JSON here…' : 'e.g. Need 500 laptops for new hires joining next month. Prefer Dell if commercially competitive. Budget around CHF 750k.'}
              value={plainText}
              onChange={(e) => setPlainText(e.target.value)}
              rows={5}
              required
              className={`resize-none transition-colors ${dragging ? 'border-primary bg-primary/5' : ''}`}
            />
          </CardContent>
        </Card>

        {/* Meta details */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Request Details</CardTitle>
            <CardDescription>Optional — fill in as much as you know.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="title">Title</Label>
                <Input
                  id="title"
                  placeholder="Auto-generated from description if left blank"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="required-by">Required By</Label>
                <Input
                  id="required-by"
                  type="date"
                  value={requiredByDate}
                  onChange={(e) => setRequiredByDate(e.target.value)}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Category L1</Label>
                <Select
                  value={categoryL1}
                  onValueChange={(v) => { setCategoryL1(v); setCategoryL2(''); }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select category" />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.keys(CATEGORIES).map((l1) => (
                      <SelectItem key={l1} value={l1}>{l1}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Category L2</Label>
                <Select
                  value={categoryL2}
                  onValueChange={setCategoryL2}
                  disabled={!categoryL1}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select subcategory" />
                  </SelectTrigger>
                  <SelectContent>
                    {l2Options.map((l2) => (
                      <SelectItem key={l2} value={l2}>{l2}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <Separator />

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="budget">Budget</Label>
                <Input
                  id="budget"
                  type="number"
                  min={0}
                  step="0.01"
                  placeholder="0.00"
                  value={budgetAmount}
                  onChange={(e) => setBudgetAmount(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Currency</Label>
                <Select value={currency} onValueChange={setCurrency}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CURRENCIES.map((c) => (
                      <SelectItem key={c} value={c}>{c}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="quantity">Quantity</Label>
                <Input
                  id="quantity"
                  type="number"
                  min={0}
                  placeholder="0"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Unit of Measure</Label>
                <Select value={unitOfMeasure} onValueChange={setUnitOfMeasure}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select unit" />
                  </SelectTrigger>
                  <SelectContent>
                    {UNIT_OPTIONS.map((u) => (
                      <SelectItem key={u} value={u}>{u}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <Separator />

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="preferred-supplier">Preferred Supplier</Label>
                <Input
                  id="preferred-supplier"
                  placeholder="Optional"
                  value={preferredSupplier}
                  onChange={(e) => setPreferredSupplier(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="incumbent-supplier">Incumbent Supplier</Label>
                <Input
                  id="incumbent-supplier"
                  placeholder="Optional"
                  value={incumbentSupplier}
                  onChange={(e) => setIncumbentSupplier(e.target.value)}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Contract Type</Label>
                <Select value={contractType} onValueChange={setContractType}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select type" />
                  </SelectTrigger>
                  <SelectContent>
                    {CONTRACT_OPTIONS.map((c) => (
                      <SelectItem key={c} value={c}>{c}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="delivery-countries">Delivery Countries</Label>
                <Input
                  id="delivery-countries"
                  placeholder="e.g. CH, DE, FR"
                  value={deliveryCountries}
                  onChange={(e) => setDeliveryCountries(e.target.value)}
                />
              </div>
            </div>

            <div className="flex flex-col gap-2 pt-1">
              <div className="flex items-center gap-2">
                <Checkbox
                  id="data-residency"
                  checked={dataResidency}
                  onCheckedChange={(v) => setDataResidency(!!v)}
                />
                <Label htmlFor="data-residency" className="cursor-pointer">Data residency constraint</Label>
              </div>
              <div className="flex items-center gap-2">
                <Checkbox
                  id="esg"
                  checked={esgRequirement}
                  onCheckedChange={(v) => setEsgRequirement(!!v)}
                />
                <Label htmlFor="esg" className="cursor-pointer">ESG requirement</Label>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Submitter info (read-only) */}
        <Card className="bg-muted/40">
          <CardHeader className="pb-2">
            <CardTitle className="text-base text-muted-foreground">Submitted by</CardTitle>
          </CardHeader>
          <CardContent className="text-sm grid grid-cols-2 sm:grid-cols-4 gap-2 text-muted-foreground">
            <div><span className="font-medium text-foreground">{user?.name}</span></div>
            <div>{user?.requester_role}</div>
            <div>{user?.business_unit}</div>
            <div>{user?.site}, {user?.country}</div>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-3">
          <Button type="button" variant="outline" onClick={() => router.back()}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? 'Submitting…' : 'Submit Request'}
          </Button>
        </div>
      </form>
    </div>
  );
}
