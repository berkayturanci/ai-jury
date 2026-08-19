class AiJury < Formula
  include Language::Python::Virtualenv

  desc "Cross-vendor multi-agent PR & code review jury"
  homepage "https://ai-jury.dev/"
  url "https://files.pythonhosted.org/packages/bf/48/b068e0a656aed03f85ca5fabca38102712ccffb9679400366aebc8a6a11d/ai_jury-1.14.3.tar.gz"
  sha256 "49442692c6835aa3bbe05d72b332ebb19810b7d7402321365f77f2ae7af20e35"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "jury 1.14.3", shell_output("#{bin}/jury --version")
    assert_match "error: provide one of", shell_output("#{bin}/jury --mock 2>&1", 1)
  end
end
